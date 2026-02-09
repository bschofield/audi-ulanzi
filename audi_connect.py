#!/usr/bin/env python3
"""
Standalone Audi Connect API client.
Extracted from audiconnect/audi_connect_ha Home Assistant integration, fixed by Claude to work with current API.

Auth flow:
1. Get OpenID config from identity.vwgroup.io
2. Perform OAuth2 PKCE flow with user credentials
3. Exchange tokens for MBB OAuth token
4. Use bearer token to access vehicle APIs

Endpoints:
- Vehicles list: https://emea.bff.cariad.digital/vehicle/v1/vehicles
"""

import asyncio
import aiohttp
import json
import hashlib
import base64
import secrets
import re
from html.parser import HTMLParser
from urllib.parse import urlencode, urlparse, parse_qs
from pathlib import Path
from datetime import datetime, timedelta

# API URLs
OPENID_CONFIG_URL = "https://emea.bff.cariad.digital/login/v1/idk/openid-configuration"
MBB_OAUTH_BASE_URL = "https://mbboauth-1d.prd.ece.vwg-connect.com/mbbcoauth"

# App credentials (from myAudi app)
CLIENT_ID = "09b6cbec-cd19-4589-82fd-363dfa8c24da@apps_vw-dilab_com"
X_CLIENT_ID = "77869e21-e30a-4a92-b016-48ab7d3db1d8"
USER_AGENT = "myAudi-Android/4.13.0 (Build 800238275.2210271555) Android/11"

# Token cache
TOKEN_FILE = Path.home() / ".audi_tokens.json"


def log(msg: str):
    """Print message with timestamp prefix."""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")



def generate_code_verifier():
    """Generate PKCE code verifier."""
    return (
        base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    )


def generate_code_challenge(verifier):
    """Generate PKCE code challenge from verifier."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class AudiConnect:
    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.session = None

        # Token storage
        self.access_token = None
        self.refresh_token = None
        self.id_token = None
        self.token_expiry = None
        self.mbb_token = None
        self.mbb_token_expiry = None

        # OpenID config
        self.openid_config = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *args):
        if self.session:
            await self.session.close()

    def _load_tokens(self):
        """Load cached tokens from file."""
        if TOKEN_FILE.exists():
            try:
                data = json.loads(TOKEN_FILE.read_text())
                self.access_token = data.get("access_token")
                self.refresh_token = data.get("refresh_token")
                self.id_token = data.get("id_token")
                self.mbb_token = data.get("mbb_token")
                if data.get("token_expiry"):
                    self.token_expiry = datetime.fromisoformat(data["token_expiry"])
                if data.get("mbb_token_expiry"):
                    self.mbb_token_expiry = datetime.fromisoformat(
                        data["mbb_token_expiry"]
                    )
                return True
            except Exception as e:
                log(f"Failed to load tokens: {e}")
        return False

    def _save_tokens(self):
        """Save tokens to file."""
        data = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "id_token": self.id_token,
            "mbb_token": self.mbb_token,
            "token_expiry": (
                self.token_expiry.isoformat() if self.token_expiry else None
            ),
            "mbb_token_expiry": (
                self.mbb_token_expiry.isoformat() if self.mbb_token_expiry else None
            ),
        }
        TOKEN_FILE.write_text(json.dumps(data, indent=2))

    async def _get_openid_config(self):
        """Fetch OpenID configuration."""
        async with self.session.get(OPENID_CONFIG_URL) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to get OpenID config: {resp.status}")
            self.openid_config = await resp.json()
            return self.openid_config

    async def login(self):
        """Perform full OAuth2 login flow."""
        log("Starting login flow...")

        # Try to load cached tokens first
        if self._load_tokens():
            if (
                self.mbb_token
                and self.mbb_token_expiry
                and datetime.now() < self.mbb_token_expiry
            ):
                log("Using cached MBB token")
                return True
            elif self.refresh_token:
                log("Attempting token refresh...")
                try:
                    await self._refresh_tokens()
                    return True
                except Exception as e:
                    log(f"Refresh failed: {e}, doing full login")

        # Get OpenID configuration
        await self._get_openid_config()

        # Generate PKCE codes
        code_verifier = generate_code_verifier()
        code_challenge = generate_code_challenge(code_verifier)

        # Step 1: Get authorization page
        auth_params = {
            "client_id": CLIENT_ID,
            "response_type": "code",
            "redirect_uri": "myaudi:///",
            "scope": "address profile badge birthdate birthplace nationalIdentifier nationality profession email vin phone nickname name picture mbb gallery openid",
            "state": secrets.token_hex(16),
            "nonce": secrets.token_hex(16),
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "ui_locales": "en-GB",
        }

        auth_url = (
            self.openid_config["authorization_endpoint"] + "?" + urlencode(auth_params)
        )

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        log("Fetching auth page...")
        async with self.session.get(
            auth_url, headers=headers, allow_redirects=False
        ) as resp:
            if resp.status == 302:
                # Follow redirect
                location = resp.headers.get("Location")
                if location.startswith("/"):
                    location = "https://identity.vwgroup.io" + location
                async with self.session.get(location, headers=headers) as resp2:
                    html = await resp2.text()
            else:
                html = await resp.text()

        # Parse form action and hidden fields from HTML
        parser = HTMLParser()
        form_action = None
        hidden_fields = {}
        def handle_starttag(tag, attrs):
            nonlocal form_action
            attrs = dict(attrs)
            if tag == "form" and form_action is None:
                form_action = attrs.get("action")
            if tag == "input" and attrs.get("type") == "hidden":
                name = attrs.get("name")
                if name:
                    hidden_fields[name] = attrs.get("value", "")
        parser.handle_starttag = handle_starttag
        parser.feed(html)

        if not form_action:
            log(f"HTML preview: {html[:2000]}")
            raise Exception("Could not find login form")

        # Make relative URLs absolute
        if form_action.startswith("/"):
            form_action = "https://identity.vwgroup.io" + form_action

        log(f"Found hidden fields: {list(hidden_fields.keys())}")

        # Step 2: Submit email
        email_data = {
            **hidden_fields,
            "email": self.username,
        }

        log(f"Submitting email to {form_action}...")
        async with self.session.post(
            form_action,
            data=email_data,
            headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
            allow_redirects=False,
        ) as resp:
            log(f"Email response status: {resp.status}")
            if resp.status in (302, 303):
                location = resp.headers.get("Location")
                log(f"Email redirect to: {location}")
                if location.startswith("/"):
                    location = "https://identity.vwgroup.io" + location
            else:
                html = await resp.text()
                if resp.status != 200:
                    log(f"Email response body: {html[:1000]}")
                raise Exception(f"Email submission failed with status {resp.status}")

        # Extract relayState from redirect URL
        parsed_url = urlparse(location)
        query_params = parse_qs(parsed_url.query)
        relay_state = query_params.get("relayState", [None])[0]

        if not relay_state:
            raise Exception(f"Could not extract relayState from: {location}")

        log(f"Got relayState: {relay_state}")

        # Step 3: GET the authenticate page to extract window._IDK data
        auth_page_url = f"https://identity.vwgroup.io/signin-service/v1/{CLIENT_ID}/login/authenticate?relayState={relay_state}&email={self.username}"
        log(f"Fetching authenticate page: {auth_page_url[:80]}...")

        async with self.session.get(auth_page_url, headers=headers) as resp:
            log(f"Authenticate page status: {resp.status}")
            auth_html = await resp.text()

            # Extract window._IDK object which contains csrf_token and templateModel
            idk_match = re.search(r"window\._IDK\s*=\s*\{", auth_html)
            if not idk_match:
                raise Exception("Could not find window._IDK in authenticate page")

            # Extract csrf_token
            csrf_match = re.search(r"csrf_token:\s*'([^']+)'", auth_html)
            if csrf_match:
                new_csrf = csrf_match.group(1)
                log(f"Found new CSRF token: {new_csrf[:50]}...")
            else:
                raise Exception("Could not find csrf_token in window._IDK")

            # Extract hmac from templateModel
            hmac_match = re.search(r'"hmac"\s*:\s*"([^"]+)"', auth_html)
            if hmac_match:
                new_hmac = hmac_match.group(1)
                log(f"Found HMAC: {new_hmac[:50]}...")
            else:
                raise Exception("Could not find hmac in templateModel")

        # POST password with the NEW csrf token and hmac from authenticate page
        auth_url = f"https://identity.vwgroup.io/signin-service/v1/{CLIENT_ID}/login/authenticate"
        password_data = {
            "relayState": relay_state,
            "email": self.username,
            "password": self.password,
            "_csrf": new_csrf,
            "hmac": new_hmac,
        }

        log(f"Submitting password to {auth_url}")
        async with self.session.post(
            auth_url,
            data=password_data,
            headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
            allow_redirects=False,
        ) as resp:
            log(f"Password response status: {resp.status}")
            location = resp.headers.get("Location", "")
            if location:
                if location.startswith("/"):
                    location = "https://identity.vwgroup.io" + location
                log(f"Redirect: {location[:100]}...")
            else:
                text = await resp.text()
                log(f"Response: {text[:500]}")
                # Check for error in response
                if "error" in text.lower() or resp.status >= 400:
                    raise Exception(f"Password submission failed: {resp.status}")

        # Follow redirect chain
        while (
            location and "code=" not in location and not location.startswith("myaudi:")
        ):
            if location.startswith("/"):
                location = "https://identity.vwgroup.io" + location
            log(f"Following: {location[:80]}...")
            async with self.session.get(
                location, headers=headers, allow_redirects=False
            ) as r:
                log(f"  Status: {r.status}")
                new_loc = r.headers.get("Location", "")
                if new_loc:
                    log(f"  Location: {new_loc[:100]}")

                # Handle consent/marketing page - need to POST to accept
                if r.status == 200 and "consent/marketing" in location:
                    log("  -> Marketing consent page, submitting acceptance...")
                    text = await r.text()

                    # Extract CSRF from page
                    csrf_match = re.search(r"csrf_token:\s*'([^']+)'", text)
                    consent_csrf = csrf_match.group(1) if csrf_match else ""

                    # The callback URL is in the query string - we need to POST then follow it
                    parsed = urlparse(location)
                    params = parse_qs(parsed.query)
                    callback_url = params.get("callback", [""])[0]
                    hmac_val = params.get("hmac", [""])[0]
                    relay = params.get("relayState", [""])[0]

                    # POST to accept marketing consent (or decline - usually works either way)
                    consent_post_url = location.split("?")[0]
                    consent_data = {
                        "_csrf": consent_csrf,
                        "relayState": relay,
                        "hmac": hmac_val,
                        "marketingPermission": "NO",  # Don't opt into marketing
                    }

                    async with self.session.post(
                        consent_post_url,
                        data=consent_data,
                        headers={
                            **headers,
                            "Content-Type": "application/x-www-form-urlencoded",
                        },
                        allow_redirects=False,
                    ) as consent_resp:
                        log(f"  Consent POST status: {consent_resp.status}")
                        new_loc = consent_resp.headers.get("Location", "")
                        if new_loc:
                            log(f"  Consent redirect: {new_loc[:100]}")
                        else:
                            # If no redirect, try the callback URL directly
                            if callback_url:
                                new_loc = callback_url
                                log(f"  Using callback URL: {new_loc[:100]}")

                if not new_loc:
                    if r.status == 200:
                        text = await r.text()
                        # Check for JS redirect
                        match = re.search(
                            r'window\.location\s*=\s*["\']([^"\']+)["\']', text
                        )
                        if match:
                            new_loc = match.group(1)
                            log(f"  JS redirect: {new_loc[:100]}")
                    break
                location = new_loc

        # Extract authorization code
        if "code=" not in location:
            raise Exception(
                f"Failed to get authorization code. Final location: {location}"
            )

        parsed = urlparse(location)
        code = parse_qs(parsed.query).get("code", [None])[0]
        if not code and parsed.fragment:
            code = parse_qs(parsed.fragment).get("code", [None])[0]

        if not code:
            raise Exception("Could not extract authorization code")

        log(f"Got authorization code: {code[:20]}...")

        # Step 4: Exchange code for tokens
        token_data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "myaudi:///",
            "client_id": CLIENT_ID,
            "code_verifier": code_verifier,
        }

        async with self.session.post(
            self.openid_config["token_endpoint"],
            data=token_data,
            headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"Token exchange failed: {resp.status} - {text}")

            tokens = await resp.json()
            self.access_token = tokens.get("access_token")
            self.refresh_token = tokens.get("refresh_token")
            self.id_token = tokens.get("id_token")
            expires_in = tokens.get("expires_in", 3600)
            self.token_expiry = datetime.now() + timedelta(seconds=expires_in)

        log("Got OAuth tokens, exchanging for MBB token...")

        # Step 5: Exchange for MBB OAuth token
        await self._get_mbb_token()

        self._save_tokens()
        log("Login successful!")
        return True

    async def _get_mbb_token(self):
        """Exchange ID token for MBB OAuth token."""
        headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Client-ID": X_CLIENT_ID,
        }

        mbb_data = {
            "grant_type": "id_token",
            "token": self.id_token,
            "scope": "sc2:fal",
        }

        async with self.session.post(
            MBB_OAUTH_BASE_URL + "/mobile/oauth2/v1/token",
            data=urlencode(mbb_data),
            headers=headers,
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"MBB token exchange failed: {resp.status} - {text}")

            mbb_tokens = await resp.json()
            self.mbb_token = mbb_tokens.get("access_token")
            expires_in = mbb_tokens.get("expires_in", 3600)
            self.mbb_token_expiry = datetime.now() + timedelta(seconds=expires_in)

    async def _refresh_tokens(self):
        """Refresh expired tokens."""
        if not self.openid_config:
            await self._get_openid_config()

        token_data = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": CLIENT_ID,
        }

        async with self.session.post(
            self.openid_config["token_endpoint"],
            data=token_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ) as resp:
            if resp.status != 200:
                raise Exception(f"Token refresh failed: {resp.status}")

            tokens = await resp.json()
            self.access_token = tokens.get("access_token")
            self.refresh_token = tokens.get("refresh_token", self.refresh_token)
            self.id_token = tokens.get("id_token")
            expires_in = tokens.get("expires_in", 3600)
            self.token_expiry = datetime.now() + timedelta(seconds=expires_in)

        await self._get_mbb_token()
        self._save_tokens()

    async def authenticated_get(self, url):
        """Authenticated GET with auto-refresh on 401."""
        for attempt in range(2):
            headers = {
                "User-Agent": USER_AGENT,
                "Authorization": f"Bearer {self.access_token}",
                "Accept": "application/json",
                "X-Client-Id": X_CLIENT_ID,
            }
            async with self.session.get(url, headers=headers) as resp:
                if resp.status == 401 and attempt == 0:
                    log("Token expired, refreshing...")
                    await self._refresh_tokens()
                    continue
                return resp.status, await resp.text()

    async def get_vehicles(self):
        """Get list of vehicles from cariad.digital API."""
        url = "https://emea.bff.cariad.digital/vehicle/v1/vehicles"
        status, text = await self.authenticated_get(url)
        if status != 200:
            raise Exception(f"Failed to get vehicles: {status} - {text}")
        return json.loads(text)


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Audi Connect API client")
    parser.add_argument("--username", "-u", required=True, help="Audi Connect username")
    parser.add_argument("--password", "-p", required=True, help="Audi Connect password")
    args = parser.parse_args()

    async with AudiConnect(args.username, args.password) as audi:
        await audi.login()

        log("Getting vehicles...")
        vehicles = await audi.get_vehicles()
        print(json.dumps(vehicles, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
