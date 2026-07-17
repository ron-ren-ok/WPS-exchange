[CmdletBinding()]
param(
    [string]$Markdown,
    [string]$CardTitle,
    [string]$CardSubtitle,
    [string]$CardText,
    [string]$WebhookUrl,
    [switch]$ValidateOnly,
    [string]$WebhookKey = $env:WPS_WEBHOOK_KEY,
    [string]$WebhookSecret = $env:WPS_WEBHOOK_SECRET
)

$ErrorActionPreference = 'Stop'

function Get-WebhookUrlFromLocalSecret {
    $secretDirectory = Join-Path $PSScriptRoot '..\webhook'
    if (-not (Test-Path -LiteralPath $secretDirectory -PathType Container)) { throw '未找到 webhook 目录。请在本 skill 的 webhook 目录放置仅包含一个 URL 的文件。' }
    $secretFiles = @(Get-ChildItem -LiteralPath $secretDirectory -File -Force)
    if ($secretFiles.Count -ne 1) { throw 'webhook 目录必须且只能包含一个地址文件。' }
    $candidate = [System.IO.File]::ReadAllText($secretFiles[0].FullName, [System.Text.UTF8Encoding]::new($false)).Trim()
    if ($candidate -notmatch '^https://365\.kdocs\.cn/.+webhook/send\?key=.+$') { throw 'webhook 地址格式无效。' }
    return $candidate
}

if ([string]::IsNullOrWhiteSpace($WebhookUrl)) { $WebhookUrl = Get-WebhookUrlFromLocalSecret }
if ($ValidateOnly) { [pscustomobject]@{ SecretLoaded = $true; Webhook = 'configured' }; return }

if ($CardTitle -or $CardSubtitle -or $CardText) {
    if ([string]::IsNullOrWhiteSpace($CardTitle) -or [string]::IsNullOrWhiteSpace($CardText)) { throw '卡片需要 CardTitle 和 CardText。' }
    $payload = @{
        msgtype = 'card'
        card = @{
            header = @{
                title = @{ tag = 'text'; content = @{ type = 'plainText'; text = $CardTitle } }
                subtitle = @{ tag = 'text'; content = @{ type = 'plainText'; text = $CardSubtitle } }
            }
            elements = @(@{ tag = 'text'; content = @{ type = 'markdown'; text = $CardText } })
        }
    }
} elseif (-not [string]::IsNullOrWhiteSpace($Markdown)) {
    $payload = @{ msgtype = 'markdown'; markdown = @{ text = $Markdown } }
} else {
    throw '请提供 Markdown，或提供 CardTitle 与 CardText。'
}

$json = $payload | ConvertTo-Json -Depth 8 -Compress
$body = [System.Text.UTF8Encoding]::new($false).GetBytes($json)
$headers = @{ 'Content-Type' = 'application/json' }

if (($WebhookKey -and -not $WebhookSecret) -or ($WebhookSecret -and -not $WebhookKey)) { throw '签名校验需要同时配置 WPS_WEBHOOK_KEY 和 WPS_WEBHOOK_SECRET。' }
if ($WebhookKey -and $WebhookSecret) {
    $md5 = [System.Security.Cryptography.MD5]::Create()
    try { $contentMd5 = ([System.BitConverter]::ToString($md5.ComputeHash($body))).Replace('-', '').ToLowerInvariant() } finally { $md5.Dispose() }
    $date = [DateTime]::UtcNow.ToString('r', [System.Globalization.CultureInfo]::InvariantCulture)
    $source = $WebhookSecret + $contentMd5 + 'application/json' + $date
    $sha1 = [System.Security.Cryptography.SHA1]::Create()
    try { $signature = ([System.BitConverter]::ToString($sha1.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($source)))).Replace('-', '').ToLowerInvariant() } finally { $sha1.Dispose() }
    $headers['Content-Md5'] = $contentMd5
    $headers['DATE'] = $date
    $headers['Authorization'] = "$WebhookKey`:$signature"
}

$response = Invoke-WebRequest -Uri $WebhookUrl -Method Post -ContentType 'application/json' -Headers $headers -Body $body -UseBasicParsing
[pscustomobject]@{ StatusCode = $response.StatusCode; Success = $true }