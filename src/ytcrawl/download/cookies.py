from __future__ import annotations

import argparse
import re

from yt_dlp.cookies import SUPPORTED_BROWSERS, SUPPORTED_KEYRINGS

BrowserCookieSpec = tuple[str, str | None, str | None, str | None]


def parse_browser_cookie_spec(value: str) -> BrowserCookieSpec:
    # Match yt-dlp's CLI syntax, but return its Python API tuple order.
    match = re.fullmatch(
        r"""(?x)
        (?P<browser>[^+:]+)
        (?:\s*\+\s*(?P<keyring>[^:]+))?
        (?:\s*:\s*(?!:)(?P<profile>.+?))?
        (?:\s*::\s*(?P<container>.+))?
        """,
        value,
    )
    if match is None:
        raise ValueError(
            "Expected BROWSER[+KEYRING][:PROFILE][::CONTAINER], "
            "for example 'chrome:Profile 1'."
        )

    browser, profile, keyring, container = match.group(
        "browser", "profile", "keyring", "container"
    )
    browser = browser.lower()
    if browser not in SUPPORTED_BROWSERS:
        raise ValueError(
            f"Unsupported browser: {browser!r}. "
            f"Choose from: {', '.join(sorted(SUPPORTED_BROWSERS))}."
        )
    if keyring is not None:
        keyring = keyring.upper()
        if keyring not in SUPPORTED_KEYRINGS:
            raise ValueError(f"Unsupported cookie keyring: {keyring!r}.")
    return browser, profile, keyring, container


def _validated_browser_cookie_spec(value: str) -> str:
    try:
        parse_browser_cookie_spec(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return value


def add_browser_cookie_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--cookies-from-browser",
        type=_validated_browser_cookie_spec,
        metavar="BROWSER[+KEYRING][:PROFILE][::CONTAINER]",
        help=(
            "Read browser cookies for video downloads (disabled by default). "
            "Use 'chrome:Profile 1' to select a profile folder or "
            "'chrome:/absolute/profile/path' to select a profile path. "
            "Without PROFILE, yt-dlp selects a profile automatically."
        ),
    )
