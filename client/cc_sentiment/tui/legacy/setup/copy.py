from __future__ import annotations

WELCOME_TITLE = "Set up cc-sentiment"
WELCOME_BODY = (
    "We'll create a verification key so we can confirm uploads are yours. "
    "This usually takes about 30 seconds."
)
WELCOME_CTA = "Get started"
WELCOME_CHECKING = "Checking your setup…"

USERNAME_PLACEHOLDER = "yasyf"
USERNAME_ERROR_EMPTY = "Enter your GitHub username, or pick “I don't use GitHub” below."
USERNAME_ERROR_NOT_FOUND = "GitHub user “{user}” wasn't found."
USERNAME_ERROR_UNREACHABLE = "Couldn't reach GitHub. Try again in a moment."
USERNAME_NO_GITHUB_LINK = "I don't use GitHub →"
USERNAME_SKIP_GPG_ONLY = "Continuing without GitHub. We'll verify by email."

ALTERNATE_TITLE = "What email should we use?"
ALTERNATE_BODY = "We'll send a one-time verification link."
ALTERNATE_CTA = "Send link"

OPENPGP_EMAIL_ERROR_EMPTY = "Use an email address you can open now."
OPENPGP_AFTER_SEND = (
    "Verification email sent to {email}. Open it, click the link, then return here."
)
OPENPGP_NO_EMAIL_NEEDED = "Your public key is published. Checking verification now."

WORKING_TITLE = "Setting up…"
WORKING_BODY = "Creating your verification gist."

PUBLISH_TITLE = "One more step"
PUBLISH_BODY = (
    "Create a public GitHub gist with what we copied to your clipboard. "
    "We'll find it automatically."
)
PUBLISH_OPEN_LABEL = "Open GitHub"
PUBLISH_COPY_AGAIN_LABEL = "Copy again"
PUBLISH_NO_GITHUB_LINK = "I don't use GitHub →"
PUBLISH_WATCH_LABEL = "Watching for your gist…"
PUBLISH_KEY_PREVIEW_TITLE = "Verification key"
MANUAL_GIST_INTRO_NO_CLIPBOARD = (
    "Copy the public key below, then paste it into a new public gist."
)

BLOCKED_TITLE = "We need an SSH client or GPG"
BLOCKED_BODY = (
    "Your system doesn't have either installed. "
    "Open the install guide, then re-run “cc-sentiment setup”."
)
BLOCKED_INSTALL_HINT_BREW = "  brew install gnupg"
BLOCKED_INSTALL_HINT_GENERIC = "Install OpenSSH or GPG, then return."

TROUBLE_TITLE = "Still watching for your gist"
TROUBLE_BODY = (
    "Sometimes GitHub takes a minute. Want to keep watching, or try a different way?"
)
TROUBLE_KEEP_WATCHING = "Keep watching"
TROUBLE_TRY_DIFFERENT = "Try a different way"

DONE_TITLE = "All set"
