import subprocess
import sys
from datetime import datetime

from app import OUTPUT_DIR, SEARCHES, run_collection


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=OUTPUT_DIR,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return result.stdout.strip()


def main():
    branch = git("symbolic-ref", "--quiet", "--short", "HEAD")
    remote = git("config", "--get", f"branch.{branch}.remote")
    target = git("config", "--get", f"branch.{branch}.merge")
    if remote == "." or not target.startswith("refs/heads/"):
        raise RuntimeError("La branche doit suivre une branche distante pour publier les annonces.")

    files = [f"annonces_{search['file_key']}.csv" for search in SEARCHES]
    files.append("index.html")
    if git("status", "--porcelain", "--", *files):
        raise RuntimeError(
            "Les CSV ou index.html contiennent des modifications non commitees. "
            "Commitez-les ou mettez-les de cote avant la collecte."
        )

    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        cwd=OUTPUT_DIR,
        check=True,
    )
    run_collection()

    if not git("status", "--porcelain", "--", *files):
        print("Aucun changement dans les annonces : aucun commit ni push.")
        return

    git("add", "--", *files)
    message = f"Update listings {datetime.now().isoformat(timespec='seconds')}"
    git(
        "commit", "--only", "-m", message,
        "-m", "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>",
        "--", *files,
    )
    git("push", remote, f"HEAD:{target}")
    print("Annonces collectees, commitees et publiees.")


if __name__ == "__main__":
    main()
