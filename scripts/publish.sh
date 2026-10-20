#!/usr/bin/env bash
# Publish nova-chatmix: GitHub repo + release (download counts) + AUR package.
# Safe to re-run: every step checks what's already done. Run from an Arch box
# (makepkg is used to verify the package and generate .SRCINFO).
#
#   scripts/publish.sh           publish the version in src/nova-chatmix.py
#
# Releasing a new version later: bump __version__ in src/nova-chatmix.py,
# set pkgver in packaging/aur/PKGBUILD (pkgrel=1), commit, run this again.
set -euo pipefail
cd "$(dirname "$0")/.."

REPO="${REPO:-Probler-Yt/nova-chatmix}"
NAME=nova-chatmix
VER=$(make -s version)
TAG="v$VER"
TARBALL="dist/$NAME-$VER.tar.gz"
DESC="Hardware ChatMix dial for the SteelSeries Arctis Nova Pro Wired on Linux, with drag-and-drop app routing"

say()  { printf '\n\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*"; exit 1; }

for c in git gh make sha256sum; do command -v "$c" >/dev/null || die "missing '$c'"; done
grep -q "^pkgver=$VER$" packaging/aur/PKGBUILD || die "PKGBUILD pkgver doesn't match $VER"
[[ -z "$(git status --porcelain)" ]] || die "commit or stash your changes first"
[[ -n "$(git config user.email || true)" ]] || \
  die "set your git identity first: git config --global user.name \"Your Name\"; git config --global user.email you@example.com"

say "GitHub login"
gh auth status >/dev/null 2>&1 || gh auth login --git-protocol https --web
gh auth setup-git >/dev/null 2>&1 || true

# First publish: make the initial commit yours (it was prepared under a placeholder address).
if ! git remote get-url origin >/dev/null 2>&1 && \
   [[ "$(git log -1 --format=%ae)" == "Probler-Yt@users.noreply.github.com" ]] && \
   [[ -n "$(git config user.email || true)" ]]; then
  git commit --amend --no-edit --reset-author --quiet
fi

say "Repository $REPO"
if ! gh repo view "$REPO" >/dev/null 2>&1; then
  gh repo create "$REPO" --public --description "$DESC"
fi
git remote get-url origin >/dev/null 2>&1 || git remote add origin "https://github.com/$REPO.git"
git push -u origin HEAD:main
gh repo edit "$REPO" --description "$DESC" \
  --add-topic linux --add-topic steelseries --add-topic arctis --add-topic arctis-nova-pro \
  --add-topic chatmix --add-topic pipewire --add-topic kde --add-topic aur >/dev/null || true

say "Release $TAG"
if ! git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  git tag -a "$TAG" -m "$NAME $VER"
fi
git push origin "$TAG"
mkdir -p dist
if gh release view "$TAG" -R "$REPO" >/dev/null 2>&1; then
  # never rebuild a published tarball - its checksum is what the AUR verifies
  gh release download "$TAG" -R "$REPO" -p "$NAME-$VER.tar.gz" -D dist --clobber
else
  make -s dist >/dev/null
  NOTES="dist/notes.md"
  awk -v v="$VER" '/^## /{p=($2==v); next} p' CHANGELOG.md > "$NOTES"
  printf '\n**Install (Arch):** `yay -S %s` - other distros: see the README.\n' "$NAME" >> "$NOTES"
  gh release create "$TAG" "$TARBALL" -R "$REPO" --title "$NAME $VER" --notes-file "$NOTES"
fi
SUM=$(sha256sum "$TARBALL" | cut -d' ' -f1)
echo "sha256 $SUM"

say "AUR package files"
sed -i "s/^sha256sums=.*/sha256sums=('$SUM')/" packaging/aur/PKGBUILD
if command -v makepkg >/dev/null; then
  BUILD=$(mktemp -d)
  cp packaging/aur/PKGBUILD packaging/aur/nova-chatmix.install "$BUILD/"
  (cd "$BUILD" && makepkg -f --noconfirm >/dev/null && echo "test build OK: $(ls ./*.pkg.tar.*)")
  (cd packaging/aur && makepkg --printsrcinfo > .SRCINFO)
  command -v namcap >/dev/null && namcap "$BUILD"/*.pkg.tar.* || true
  rm -rf "$BUILD"
else
  warn "makepkg not found: updating .SRCINFO by hand"
  sed -i "s/^\tsha256sums = .*/\tsha256sums = $SUM/" packaging/aur/.SRCINFO
fi
if [[ -n "$(git status --porcelain packaging/aur)" ]]; then
  git add packaging/aur/PKGBUILD packaging/aur/.SRCINFO
  git commit -q -m "aur: $VER checksum"
  git push origin HEAD:main
fi

say "AUR upload"
rc=0
ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new aur@aur.archlinux.org list-repos >/dev/null 2>&1 || rc=$?
if [[ $rc -eq 255 ]]; then  # 255 = ssh itself failed: no key / key not on the AUR
  warn "Your SSH key isn't set up with the AUR yet. One-time setup:"
  echo "    1. Make an account at https://aur.archlinux.org/register"
  echo "    2. Paste your public key into My Account > SSH Public Key:"
  echo "         $(cat ~/.ssh/id_ed25519.pub 2>/dev/null || echo 'ssh-keygen -t ed25519   # then: cat ~/.ssh/id_ed25519.pub')"
  echo "    3. Run scripts/publish.sh again - everything else is already done."
  exit 0
fi
AUR=$(mktemp -d)
git -c init.defaultBranch=master clone -q "ssh://aur@aur.archlinux.org/$NAME.git" "$AUR"
if [[ -f "$AUR/PKGBUILD" ]] && ! grep -q "github.com/$REPO" "$AUR/PKGBUILD"; then
  die "The AUR name '$NAME' belongs to someone else - rename the package (pkgname) and retry."
fi
cp packaging/aur/PKGBUILD packaging/aur/.SRCINFO packaging/aur/nova-chatmix.install "$AUR/"
cd "$AUR"
git add PKGBUILD .SRCINFO nova-chatmix.install
if git diff --cached --quiet; then
  echo "AUR already up to date."
else
  git commit -q -m "Update to $VER"
  git push -q origin HEAD:master
  echo "Published: https://aur.archlinux.org/packages/$NAME"
fi
rm -rf "$AUR"

say "Done"
echo "  Repo:     https://github.com/$REPO"
echo "  Release:  https://github.com/$REPO/releases/tag/$TAG"
echo "  AUR:      https://aur.archlinux.org/packages/$NAME"
echo "  Stats:    scripts/stats.sh"
