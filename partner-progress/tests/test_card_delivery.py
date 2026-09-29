"""Exercise the real workflow send block and sender with network calls mocked."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PWSH = shutil.which("pwsh")


def ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(PWSH, "PowerShell is required for delivery integration checks")
class CardDeliveryTests(unittest.TestCase):
    def run_ps(self, script):
        completed = subprocess.run(
            [PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def send_workflow(self, fail_first=False):
        workflow = (ROOT / ".github/workflows/partner-progress.yml").read_text(encoding="utf-8")
        send_block = workflow.split("        run: |\n", 1)[1]
        send_block = "\n".join(line[10:] for line in send_block.splitlines())
        send_block = send_block.replace("./scripts/send-wps-webhook.ps1", "Send-TestCard")
        with tempfile.TemporaryDirectory() as temp:
            Path(temp, "wps-revenue-partners.md").write_text("revenue body", encoding="utf-8")
            Path(temp, "wps-new-partners.md").write_text("new body", encoding="utf-8")
            script = """
                $ErrorActionPreference = 'Stop'
                $events = [System.Collections.Generic.List[string]]::new()
                function Send-TestCard($CardTitle, $CardSubtitle, $CardText, $WebhookUrl) {
                    $events.Add("send:$CardTitle|$CardText")
                    if ($script:failFirst -and $events.Count -eq 1) { throw 'mock rejection' }
                }
                function Start-Sleep($Seconds) { $events.Add("sleep:$Seconds") }
                $failed = $false
            """
            script += f"\n$env:RUNNER_TEMP = {ps_quote(temp)}\n"
            script += "$env:WPS_WEBHOOK_URL = 'https://example.invalid/test'\n"
            script += f"$script:failFirst = ${str(fail_first).lower()}\n"
            script += "try {\n" + send_block + "\n} catch { $failed = $true }\n"
            script += "@{events=@($events.ToArray()); failed=$failed} | ConvertTo-Json -Compress"
            return self.run_ps(script)

    def test_actual_workflow_sends_revenue_then_waits_three_seconds_then_new(self):
        result = self.send_workflow()
        self.assertFalse(result["failed"])
        self.assertEqual(result["events"], ["send:📊血量合作方日报|revenue body",
                                            "sleep:3", "send:📊新增合作方日报|new body"])

    def test_first_send_failure_does_not_start_delay_or_second_send(self):
        result = self.send_workflow(fail_first=True)
        self.assertTrue(result["failed"])
        self.assertEqual(result["events"], ["send:📊血量合作方日报|revenue body"])

    def test_sender_preserves_markdown_and_rejects_application_errors(self):
        sender = ROOT / "partner-progress/scripts/send-wps-webhook.ps1"
        body = "> <font color='#000000'>**Opera** </font>\n\n新增：**0.04** "
        footer = "<font color='#d4dae2'>┃</font> <font color='#808080'>绝对值｜当日环比｜本期7日均环比｜上期7日均环比</font>\n\n[查看明细](https://example.invalid/sheet)"
        markdown = body + "\n\n---\n\n" + footer
        for response, rejected in [('{"code":0}', False), ('{"result":"ok"}', False),
                                   ('{"code":403}', True), ('{"success":false}', True),
                                   ('{"result":"error"}', True), ('not json', True)]:
            with self.subTest(response=response):
                script = """
                    $ErrorActionPreference = 'Stop'
                    function Invoke-WebRequest {
                        param($Uri, $Method, $ContentType, $Headers, $Body, [switch]$UseBasicParsing)
                        $global:testPayload = [System.Text.Encoding]::UTF8.GetString($Body) | ConvertFrom-Json
                        [pscustomobject]@{StatusCode=200; Content=$global:testResponse}
                    }
                    $rejected = $false
                """
                script += f"\n$global:testResponse = {ps_quote(response)}\n"
                script += f"try {{ & {ps_quote(sender)} -CardTitle 'test' -CardText {ps_quote(markdown)} "
                script += "-WebhookUrl 'https://example.invalid/test' -WebhookKey '' -WebhookSecret '' | Out-Null } "
                script += "catch { $rejected=$true }\n"
                script += "@{rejected=$rejected; payload=$global:testPayload} | ConvertTo-Json -Depth 8 -Compress"
                result = self.run_ps(script)
                self.assertEqual(result["rejected"], rejected)
                self.assertEqual(result["payload"]["msgtype"], "card")
                elements = result["payload"]["card"]["elements"]
                self.assertEqual([element["tag"] for element in elements], ["text", "hr", "text"])
                self.assertEqual(elements[0]["content"]["text"], body)
                self.assertEqual(elements[1]["style"], "solid")
                self.assertEqual(elements[2]["content"]["text"], footer)


if __name__ == "__main__":
    unittest.main()
