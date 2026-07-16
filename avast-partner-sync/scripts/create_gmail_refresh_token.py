"""One-time local Gmail OAuth authorization for the Avast GitHub Action."""
import argparse
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPE = ["https://www.googleapis.com/auth/gmail.readonly"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--client-json", required=True, help="Downloaded OAuth desktop-client JSON")
    parser.add_argument("--output", required=True, help="Local file to receive the refresh token")
    args = parser.parse_args()
    flow = InstalledAppFlow.from_client_secrets_file(Path(args.client_json), SCOPE)
    credentials = flow.run_local_server(host="localhost", port=0, open_browser=True, access_type="offline", prompt="consent")
    if not credentials.refresh_token:
        raise RuntimeError("Google did not return a refresh token; revoke the app grant and run again.")
    output = Path(args.output)
    output.write_text(credentials.refresh_token + "\n", encoding="utf-8")
    print(f"Refresh token saved locally to: {output}")


if __name__ == "__main__":
    main()