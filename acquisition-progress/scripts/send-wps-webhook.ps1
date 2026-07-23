[CmdletBinding()]
param([Parameter(Mandatory)][string]$CardTitle, [string]$CardSubtitle, [Parameter(Mandatory)][string]$CardText, [Parameter(Mandatory)][string]$WebhookUrl)
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($WebhookUrl)) { throw 'Webhook URL is required.' }
$payload = @{ msgtype = 'card'; card = @{ header = @{ title = @{ tag = 'text'; content = @{ type = 'plainText'; text = $CardTitle } }; subtitle = @{ tag = 'text'; content = @{ type = 'plainText'; text = $CardSubtitle } } }; elements = @(@{ tag = 'text'; content = @{ type = 'markdown'; text = $CardText } }) } }
$response = Invoke-WebRequest -Uri $WebhookUrl -Method Post -ContentType 'application/json' -Body ($payload | ConvertTo-Json -Depth 8 -Compress) -UseBasicParsing
[pscustomobject]@{ StatusCode = $response.StatusCode; Success = $true }
