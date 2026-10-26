"""Create and fully process a case from a folder, e.g. to pre-load the demo before presenting.

    docker compose -f deploy/docker-compose.yml exec app python -m scripts.seed_case \
        reviewer icip-trip /data/seed --self-name "Jane Smith" --self-alias "Jane" \
        --self-email jane.smith@example.com --self-email jane.smith@university.edu
(copy the folder into the container first: docker compose cp ./uploads app:/data/seed)
"""
import argparse, time
from pathlib import Path
from app import casefiles as cf
from app.jobs import start_job, current_job, last_jobs, PIPELINE

ap = argparse.ArgumentParser()
ap.add_argument("user"); ap.add_argument("case"); ap.add_argument("folder", type=Path)
ap.add_argument("--self-name", required=True); ap.add_argument("--self-alias", action="append", default=[])
ap.add_argument("--self-email", action="append", default=[]); ap.add_argument("--chat-tz", type=float, default=5.5)
a = ap.parse_args()
root = cf.save_case(a.user, a.case, {"self_name": a.self_name, "self_aliases": a.self_alias,
                                     "self_emails": a.self_email, "chat_tz": a.chat_tz})
for f in sorted(p for p in a.folder.rglob("*") if p.is_file()):
    cf.save_upload(root, f.name, f.read_bytes())
start_job(a.user, a.case, PIPELINE)
while (j := current_job(a.user, a.case)):
    print(f"\r{j['step']}: {j['message']}".ljust(80), end="", flush=True)
    time.sleep(1)
j = last_jobs(a.user, a.case, 1)[0]
print("\n" + "\n".join(j["log"][-8:]))
