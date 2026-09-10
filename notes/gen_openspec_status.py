"""
gen_openspec_status.py
Aggregates every openspec/changes/*/tasks.md into one committed status table
(openspec/STATUS.md), so there's a single-glance view of every task across
every change without opening each tasks.md individually.

Re-run this after updating any change's tasks.md:
  python notes/gen_openspec_status.py

Parsing lives in monitor/taskparse.py (shared with the heartbeat hook and
the monitor server) so the number in STATUS.md and the number on the phone
always agree. Standard library only. ASCII only.
"""

import datetime
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "monitor"))
import taskparse  # noqa: E402

CHANGES_DIR = os.path.join(REPO_ROOT, "openspec", "changes")
OUTPUT_PATH = os.path.join(REPO_ROOT, "openspec", "STATUS.md")


def md_escape(text):
    return text.replace("|", "\\|")


def main():
    if not os.path.isdir(CHANGES_DIR):
        print("No openspec/changes directory; nothing to do.")
        return 0

    now = datetime.datetime.now()
    summary_rows = []   # (name, done, total, status, mtime)
    detail_sections = []

    for name in sorted(os.listdir(CHANGES_DIR)):
        change_dir = os.path.join(CHANGES_DIR, name)
        if not os.path.isdir(change_dir) or name == "archive":
            continue
        tasks_path = os.path.join(change_dir, "tasks.md")
        if not os.path.exists(tasks_path):
            summary_rows.append((name, None, None, "no tasks.md yet", 0))
            continue

        groups = taskparse.parse_tasks_md(tasks_path)
        done, total = taskparse.progress(groups)
        status = "Complete" if total > 0 and done == total else "%d/%d" % (done, total)
        mtime = os.path.getmtime(tasks_path)
        summary_rows.append((name, done, total, status, mtime))

        section = ["## %s\n" % name, "`%d/%d` tasks complete.\n" % (done, total)]
        for group_name, tasks in groups:
            section.append("### %s\n" % group_name)
            section.append("| Done | Task |")
            section.append("|---|---|")
            for d, text in tasks:
                mark = "x" if d else " "
                section.append("| [%s] | %s |" % (mark, md_escape(text)))
            section.append("")
        detail_sections.append((mtime, "\n".join(section)))

    # Most-recently-touched tasks.md first -- surfaces active work, not A-Z.
    summary_rows.sort(key=lambda r: r[4], reverse=True)
    detail_sections.sort(key=lambda s: s[0], reverse=True)

    total_done = sum(d for _, d, t, _, _ in summary_rows if d is not None)
    total_all = sum(t for _, d, t, _, _ in summary_rows if t is not None)

    lines = []
    lines.append("# OpenSpec task status (generated)\n")
    lines.append(
        "Generated %s. Regenerate with "
        "`python notes/gen_openspec_status.py` after updating any change's "
        "`tasks.md`. Do not hand-edit this file.\n" % now.strftime("%Y-%m-%d %H:%M")
    )
    lines.append(
        "**Overall: %d/%d tasks complete across %d changes.**\n"
        % (total_done, total_all, len(summary_rows))
    )

    lines.append("## Summary (most recently updated first)\n")
    lines.append("| Change | Status | Last updated |")
    lines.append("|---|---|---|")
    for name, done, total, status, mtime in summary_rows:
        when = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d") if mtime else "-"
        lines.append("| [%s](changes/%s/tasks.md) | %s | %s |" % (name, name, status, when))
    lines.append("")

    lines.append("\n---\n")
    lines.append("\n".join(section for _, section in detail_sections))

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("Wrote %s" % OUTPUT_PATH)
    print("Overall: %d/%d" % (total_done, total_all))
    for name, done, total, status, _ in summary_rows:
        print("  %s: %s" % (name, status))
    return 0


if __name__ == "__main__":
    sys.exit(main())
