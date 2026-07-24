#!/usr/bin/env python3
"""
rebrand.py — Rebrand a local clone of markschron/edit from "Markschron" to "markschron"
without breaking anything that depends on the string "markschron" to actually
function (live content fetches, the published npm package, git remotes,
deploy platform env vars, etc).

WHY THIS ISN'T A FIND-AND-REPLACE
----------------------------------
The markschron/edit repo is the EDITOR/FRONTEND for the Markschron wiki. It does not
contain the wiki's actual text. At runtime it fetches live markdown from
FMHY's real GitHub repo (raw.githubusercontent.com/fmhy/edit/main/docs/...)
and their real search API (api.fmhy.net). If you rename those hostnames/
paths, the fetches point at resources that don't exist under your name,
and pages render empty. This happens silently — no build error, no crash,
just missing content in production.

Similarly:
  - "@fmhy/components" in package.json is a REAL PUBLISHED NPM PACKAGE.
    Renaming the string breaks `npm install` (no such package exists
    under a new scope) even though it LOOKS like a brand string.
  - .git/config remote URLs, if rewritten, break `git pull`/`push`.
  - Env var NAMES (e.g. FMHY_DISCORD_WEBHOOK) referenced in code must
    match whatever is actually configured in your Vercel dashboard.
    Renaming the string in code without renaming it in Vercel breaks
    the feature at runtime (undefined env var).

So every match is classified into one of three buckets, not just
replaced blindly:

  PROTECT  -> never touched. Logged with a reason so you know WHY.
  REVIEW   -> not touched. Written to a report for you to decide by hand
              (these are cases where "correct" depends on facts I can't
              know from reading code alone — e.g. do you own markschron.net,
              do you have your own Discord webhook, etc).
  REPLACE  -> safe cosmetic text. Auto-rewritten, case-form preserved.

USAGE
-----
    python rebrand.py --dry-run /path/to/your/cloned/repo
        -> scans only, writes rebrand_report.md, changes NOTHING

    python rebrand.py --apply /path/to/your/cloned/repo
        -> makes a full backup copy first, then applies REPLACE-tier
           changes only. REVIEW items are still left alone and listed
           in the report for you to handle manually.

ALWAYS run --dry-run first and read rebrand_report.md before --apply.
"""

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OLD_NAME_FORMS = {
    # "markschron" is an acronym so ALL-CAPS reads naturally in headers/badges
    # ("# Markschron", topic tags). "markschron" is not an acronym, so its
    # all-caps form ("MARKSCHRON") looks like shouting in prose/comments.
    # We map Markschron -> Markschron (title case) everywhere by default, which
    # reads correctly in both headers and inline text. If you specifically
    # want a shouty all-caps wordmark for a logo/header, do that by hand
    # in the one or two places you want it styled that way.
    "markschron": "markschron",
    "Markschron": "Markschron",
    "Markschron": "Markschron",
}
# "markschron" is the full expansion of the Markschron acronym. It is only
# ever used as descriptive/display text (repo topic tag, About text), never
# as part of a functional path or hostname, so it is always REPLACE-tier.
FULL_NAME_OLD = "markschron"
FULL_NAME_NEW = "markschron"  # no natural expansion given, so we mirror it

# Directories we should never walk into at all.
SKIP_DIRS = {
    ".git",
    "node_modules",
    ".next",
    ".nuxt",
    ".output",
    "dist",
    "build",
    ".vercel",
    ".wrangler",
    ".devcontainer",
    "__pycache__",
}

# Files whose *contents* we skip entirely because they are generated/locked
# and hand-editing them causes install failures or checksum mismatches.
SKIP_FILES = {
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "flake.lock",
}

# Binary / non-text extensions: don't even attempt to read as text.
BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".eot",
    ".zip", ".gz", ".tar",
}

TEXT_LIKE_EXTS = {
    ".ts", ".tsx", ".js", ".jsx", ".vue", ".mjs", ".cjs",
    ".json", ".jsonc", ".md", ".mdx", ".yaml", ".yml", ".toml",
    ".html", ".css", ".txt", ".env.example",
}


# ---------------------------------------------------------------------------
# Classification rules
# ---------------------------------------------------------------------------
#
# Order matters: rules are checked top to bottom against the FULL LINE the
# match occurs on (not just the match itself), because whether a string is
# "the brand" or "load-bearing" depends on context around it, not the string
# alone. First matching rule wins.

@dataclass
class Rule:
    name: str
    pattern: re.Pattern
    verdict: str  # "PROTECT" | "REVIEW"
    reason: str


PROTECT_RULES = [
    Rule(
        name="raw_githubusercontent_content_fetch",
        pattern=re.compile(r"raw\.githubusercontent\.com/markschron", re.IGNORECASE),
        verdict="PROTECT",
        reason=(
            "Live content fetch URL. This pulls the actual wiki markdown "
            "at runtime from Markschron's real repo. Renaming the path makes it "
            "404 silently -> empty pages in production."
        ),
    ),
    Rule(
        name="markschron_search_api",
        pattern=re.compile(r"api\.markschron\.net", re.IGNORECASE),
        verdict="PROTECT",
        reason=(
            "Markschron's real backend search API host. This is infrastructure "
            "you don't own/control; it's not a brand string, it's a "
            "dependency address."
        ),
    ),
    Rule(
        name="npm_scoped_package",
        pattern=re.compile(r'@markschron/[a-z0-9._-]+', re.IGNORECASE),
        verdict="PROTECT",
        reason=(
            "This is a published npm package name (e.g. @fmhy/components), "
            "not display branding. `npm install` / `pnpm install` will "
            "fail if this string is changed, because no package exists "
            "under a renamed scope."
        ),
    ),
    Rule(
        name="git_remote_config",
        pattern=re.compile(r"url\s*=\s*.*github\.com[:/]markschron", re.IGNORECASE),
        verdict="PROTECT",
        reason=(
            ".git remote URL. Changing this breaks `git pull`/`git push` "
            "against the real upstream/origin."
        ),
    ),
    Rule(
        name="wiki_backup_source",
        pattern=re.compile(r"github\.com/markschron/Markschron/wiki", re.IGNORECASE),
        verdict="PROTECT",
        reason=(
            "Points at Markschron's real wiki/backup source repo, used as a data "
            "or reference source, not a brand mention of your fork."
        ),
    ),
]

REVIEW_RULES = [
    Rule(
        name="env_var_name",
        pattern=re.compile(r'\bMarkschron_[A-Z0-9_]+\b'),
        verdict="REVIEW",
        reason=(
            "Looks like an environment variable NAME read via process.env "
            "or similar. If you rename this string in code, you must also "
            "rename the actual variable in your Vercel project settings, "
            "or the app will read `undefined` at runtime. Not auto-changed "
            "because I can't see your Vercel dashboard from here."
        ),
    ),
    Rule(
        name="discord_webhook_or_secret_looking",
        pattern=re.compile(r"(webhook|secret|token|api[_-]?key)", re.IGNORECASE),
        verdict="REVIEW",
        reason=(
            "Line mentions webhook/secret/token/api key alongside the "
            "brand string. Flagged so you can confirm this isn't a literal "
            "credential or an identifier tied to a specific external "
            "service (e.g. a Discord webhook registered under the FMHY "
            "name) before any text on this line is touched."
        ),
    ),
    Rule(
        name="bare_domain_reference",
        pattern=re.compile(r"markschron\.net", re.IGNORECASE),
        verdict="REVIEW",
        reason=(
            "References the live fmhy.net domain. Safe to reword as plain "
            "text (e.g. in a README), but if this is used as an actual "
            "outbound link/canonical URL in metadata, it should point "
            "somewhere you control (e.g. your own domain) rather than "
            "be text-swapped to a domain you may not own. Decide per-"
            "occurrence."
        ),
    ),
    Rule(
        name="license_or_legal",
        pattern=re.compile(r"(license|copyright|attribution)", re.IGNORECASE),
        verdict="REVIEW",
        reason=(
            "Line involves license/copyright/attribution text. Original "
            "author credit for open-source code you're building on is "
            "often expected to stay intact even after a rebrand — this is "
            "a judgment call, not a code-breakage risk, so it's left for "
            "you."
        ),
    ),
]


def classify_line(line: str) -> tuple[str, str | None, str | None]:
    """
    Return (verdict, rule_name, reason) for a line containing a brand match.
    Checks PROTECT rules first (highest priority), then REVIEW rules.
    Falls through to REPLACE if nothing matches.
    """
    for rule in PROTECT_RULES:
        if rule.pattern.search(line):
            return "PROTECT", rule.name, rule.reason
    for rule in REVIEW_RULES:
        if rule.pattern.search(line):
            return "REVIEW", rule.name, rule.reason
    return "REPLACE", None, None


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

BRAND_PATTERN = re.compile(
    r"markschron|markschron",
    re.IGNORECASE,
)


@dataclass
class Match:
    filepath: Path
    line_no: int
    line_text: str
    verdict: str
    rule_name: str | None
    reason: str | None


@dataclass
class ScanResult:
    matches: list[Match] = field(default_factory=list)
    files_scanned: int = 0
    files_skipped_binary: int = 0
    files_skipped_locked: int = 0

    def by_verdict(self, verdict: str) -> list[Match]:
        return [m for m in self.matches if m.verdict == verdict]


def should_skip_dir(dirname: str) -> bool:
    return dirname in SKIP_DIRS or dirname.startswith(".")


def is_probably_text_file(path: Path) -> bool:
    if path.suffix.lower() in BINARY_EXTS:
        return False
    if path.suffix.lower() in TEXT_LIKE_EXTS:
        return True
    # Fallback: sniff first 1024 bytes for null bytes (binary indicator).
    try:
        with open(path, "rb") as f:
            chunk = f.read(1024)
        return b"\x00" not in chunk
    except OSError:
        return False


def scan_repo(root: Path) -> ScanResult:
    result = ScanResult()

    for dirpath, dirnames, filenames in root.walk() if hasattr(root, "walk") else _walk_compat(root):
        dirnames[:] = [d for d in dirnames if not should_skip_dir(d)]

        for filename in filenames:
            filepath = dirpath / filename

            if filename in SKIP_FILES:
                result.files_skipped_locked += 1
                continue

            if not is_probably_text_file(filepath):
                result.files_skipped_binary += 1
                continue

            try:
                text = filepath.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                result.files_skipped_binary += 1
                continue

            if not BRAND_PATTERN.search(text):
                continue

            result.files_scanned += 1
            for line_no, line in enumerate(text.splitlines(), start=1):
                if BRAND_PATTERN.search(line):
                    verdict, rule_name, reason = classify_line(line)
                    result.matches.append(
                        Match(
                            filepath=filepath,
                            line_no=line_no,
                            line_text=line.strip(),
                            verdict=verdict,
                            rule_name=rule_name,
                            reason=reason,
                        )
                    )

    return result


def _walk_compat(root: Path):
    """Path.walk() is 3.12+; fall back to os.walk for older Python."""
    import os
    for dirpath, dirnames, filenames in os.walk(root):
        yield Path(dirpath), dirnames, filenames


# ---------------------------------------------------------------------------
# Replacement
# ---------------------------------------------------------------------------

def apply_replacements(text: str) -> str:
    """
    Apply case-preserving brand replacement to a full REPLACE-tier line.
    Order matters: longest/most-specific forms first so we don't partially
    match inside a longer form.
    """
    result = text
    result = result.replace(FULL_NAME_OLD, FULL_NAME_NEW)
    result = result.replace(FULL_NAME_OLD.upper(), FULL_NAME_NEW.upper())
    result = result.replace(FULL_NAME_OLD.capitalize(), FULL_NAME_NEW.capitalize())

    for old, new in OLD_NAME_FORMS.items():
        result = result.replace(old, new)

    return result


def rewrite_file(filepath: Path, protect_review_line_numbers: set[int]) -> int:
    """
    Rewrite only REPLACE-tier lines in a file. Lines whose numbers are in
    protect_review_line_numbers are left completely untouched, byte for
    byte. Returns count of lines actually changed.
    """
    original_text = filepath.read_text(encoding="utf-8")
    lines = original_text.splitlines(keepends=True)
    changed = 0

    for i, line in enumerate(lines):
        line_no = i + 1
        if line_no in protect_review_line_numbers:
            continue
        if not BRAND_PATTERN.search(line):
            continue
        new_line = apply_replacements(line)
        if new_line != line:
            lines[i] = new_line
            changed += 1

    if changed:
        filepath.write_text("".join(lines), encoding="utf-8")

    return changed


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def write_report(root: Path, result: ScanResult, applied: bool, report_path: Path) -> None:
    protect = result.by_verdict("PROTECT")
    review = result.by_verdict("REVIEW")
    replace = result.by_verdict("REPLACE")

    lines = []
    lines.append(f"# Rebrand Report — markschron → markschron")
    lines.append("")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"Repo root: `{root}`")
    lines.append(f"Mode: {'APPLIED' if applied else 'DRY RUN (no files changed)'}")
    lines.append("")
    lines.append(f"- Files scanned (containing a match): {result.files_scanned}")
    lines.append(f"- Files skipped (binary/unreadable): {result.files_skipped_binary}")
    lines.append(f"- Files skipped (lockfiles, never touched): {result.files_skipped_locked}")
    lines.append(f"- Total matching lines: {len(result.matches)}")
    lines.append(f"  - PROTECT (never touched): {len(protect)}")
    lines.append(f"  - REVIEW (needs your decision): {len(review)}")
    lines.append(f"  - REPLACE ({'changed' if applied else 'would change'}): {len(replace)}")
    lines.append("")
    lines.append(
        "Note: `.git/`, `node_modules/`, and other build/dependency "
        "directories are never walked into at all (not scanned, not "
        "classified) — this includes `.git/config`, so your git remote "
        "URL is guaranteed untouched by construction, not just by rule."
    )
    lines.append("")

    def section(title: str, matches: list[Match], note: str) -> None:
        lines.append(f"## {title} ({len(matches)})")
        lines.append("")
        lines.append(note)
        lines.append("")
        if not matches:
            lines.append("_none found_")
            lines.append("")
            return
        by_file: dict[Path, list[Match]] = {}
        for m in matches:
            by_file.setdefault(m.filepath, []).append(m)
        for fp, ms in sorted(by_file.items()):
            rel = fp.relative_to(root)
            lines.append(f"### `{rel}`")
            for m in sorted(ms, key=lambda x: x.line_no):
                if m.rule_name:
                    lines.append(f"- **Line {m.line_no}** (`{m.rule_name}`): {m.reason}")
                else:
                    lines.append(f"- **Line {m.line_no}**: cosmetic text, no functional dependency detected")
                lines.append(f"  ```")
                lines.append(f"  {m.line_text}")
                lines.append(f"  ```")
            lines.append("")

    section(
        "PROTECT — never touched, by design",
        protect,
        "These lines are functional dependencies (live URLs, published "
        "package names, git remotes). Changing them breaks the app. "
        "The script will not touch these no matter what mode you run.",
    )
    section(
        "REVIEW — left alone, decide by hand",
        review,
        "These need context I don't have (your Vercel env var names, "
        "whether you own a given domain, whether you registered your own "
        "webhook, whether you want to keep original license attribution). "
        "Nothing here was changed. Go through each one manually.",
    )
    section(
        "REPLACE — cosmetic text" + (", changed" if applied else ", would be changed in --apply"),
        replace,
        "Display text: titles, README prose, UI labels, meta tags. Safe "
        "to auto-rewrite because nothing reads these strings to construct "
        "a URL, package name, or config lookup.",
    )

    report_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rebrand a local markschron/edit clone to markschron, safely."
    )
    parser.add_argument("repo_path", type=str, help="Path to your cloned repo")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run", action="store_true",
        help="Scan and write rebrand_report.md only. Changes nothing.",
    )
    mode.add_argument(
        "--apply", action="store_true",
        help="Back up the repo, then apply REPLACE-tier changes.",
    )
    parser.add_argument(
        "--no-backup", action="store_true",
        help="Skip the automatic backup copy before --apply. Not recommended.",
    )
    args = parser.parse_args()

    root = Path(args.repo_path).resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning {root} ...")
    result = scan_repo(root)

    if not result.matches:
        print("No occurrences of 'markschron' or 'markschron' found. Nothing to do.")
        sys.exit(0)

    protect_ct = len(result.by_verdict("PROTECT"))
    review_ct = len(result.by_verdict("REVIEW"))
    replace_ct = len(result.by_verdict("REPLACE"))
    print(f"Found {len(result.matches)} matching lines across {result.files_scanned} files:")
    print(f"  PROTECT: {protect_ct}  (never touched)")
    print(f"  REVIEW:  {review_ct}  (flagged, not touched)")
    print(f"  REPLACE: {replace_ct}  ({'will be' if args.apply else 'would be'} changed)")

    if args.dry_run:
        report_path = root / "rebrand_report.md"
        write_report(root, result, applied=False, report_path=report_path)
        print(f"\nDry run complete. Read {report_path} before running --apply.")
        return

    # --apply from here on
    if not args.no_backup:
        backup_dir = root.parent / f"{root.name}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        print(f"Backing up repo to {backup_dir} before making changes ...")
        shutil.copytree(root, backup_dir, ignore=shutil.ignore_patterns(".git"))
        print(f"Backup complete: {backup_dir}")
    else:
        print("WARNING: --no-backup passed. Proceeding without a safety copy.")

    protect_or_review_lines: dict[Path, set[int]] = {}
    for m in result.matches:
        if m.verdict in ("PROTECT", "REVIEW"):
            protect_or_review_lines.setdefault(m.filepath, set()).add(m.line_no)

    replace_files = {m.filepath for m in result.by_verdict("REPLACE")}

    total_changed_lines = 0
    for filepath in sorted(replace_files):
        protected = protect_or_review_lines.get(filepath, set())
        changed = rewrite_file(filepath, protected)
        total_changed_lines += changed
        if changed:
            print(f"  updated {filepath.relative_to(root)} ({changed} line(s))")

    report_path = root / "rebrand_report.md"
    write_report(root, result, applied=True, report_path=report_path)

    print(f"\nApplied {total_changed_lines} line change(s) across {len(replace_files)} file(s).")
    print(f"PROTECT and REVIEW lines were left untouched — see {report_path}.")
    print("\nNext steps:")
    print("  1. Read rebrand_report.md, section 'REVIEW', and decide each item by hand.")
    print("  2. Run your build locally (npm run build / pnpm build) before deploying.")
    print("  3. Diff the changes (git diff) to sanity-check before committing.")


if __name__ == "__main__":
    main()
