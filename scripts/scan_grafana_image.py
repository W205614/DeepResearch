"""Collect complete pinned native/full Grafana reports; the independent checker decides disposition."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.check_native_jvm import executable, validate_database, run, digest  # noqa: E402
from scripts.grafana_image_disposition import load_binding, validate_loaded, validate_sbom  # noqa: E402


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image",required=True)
    parser.add_argument("--output-dir",required=True,type=Path)
    parser.add_argument("--tool-dir",type=Path,default=ROOT/".cache/native-jvm/tools")
    parser.add_argument("--db-cache",type=Path,default=ROOT/".cache/native-jvm/grype-db")
    args=parser.parse_args()
    args.output_dir.mkdir(parents=True,exist_ok=False)
    result={"operational_success":False,"security_gate_claim":False}
    try:
        binding=load_binding()
        lock=json.loads((ROOT/"deployment/security/native-jvm-tools.json").read_text(encoding="utf-8"))
        syft=executable("syft",lock,args.tool_dir,args.tool_dir)
        grype=executable("grype",lock,args.tool_dir,args.tool_dir)
        env={k:v for k,v in os.environ.items() if not k.startswith(("GRYPE_","SYFT_"))}
        env.update(GRYPE_DB_CACHE_DIR=str(args.db_cache.resolve()),GRYPE_DB_AUTO_UPDATE="false",GRYPE_CHECK_FOR_APP_UPDATE="false",SYFT_CHECK_FOR_APP_UPDATE="false")
        db=args.db_cache/"6/vulnerability.db"
        if not db.exists():
            run([grype,"db","import",lock["database"]["url"]],env=env,timeout=900)
        validate_database(db,lock["database"])
        actual=json.loads(run(["docker","image","inspect",args.image]))[0]
        validate_loaded(actual,binding)
        (args.output_dir/"image-inspect.json").write_text(json.dumps(actual,indent=2),encoding="utf-8")
        syconfig=args.output_dir/"syft-config.json"
        syconfig.write_text(json.dumps({"exclude":[],"check-for-app-update":False}),encoding="utf-8")
        grconfig=args.output_dir/"grype-config.json"
        grconfig.write_text(json.dumps({"ignore":[],"only-fixed":False,"only-notfixed":False,"check-for-app-update":False}),encoding="utf-8")
        sbom=args.output_dir/"full-syft.json"
        report=args.output_dir/"full-grype.json"
        run([syft,"--config",syconfig,"scan","docker:"+actual["Id"],"-o","syft-json="+str(sbom),"--quiet"],env=env)
        types=validate_sbom(json.loads(sbom.read_text(encoding="utf-8")),binding)
        run([grype,"--config",grconfig,"sbom:"+str(sbom),"-o","json","--file",report,"--quiet"],env=env)
        result.update(operational_success=True,actual_image_id=actual["Id"],syft_types=types,sbom_sha256=digest(sbom),raw_grype_sha256=digest(report),database=lock["database"],required_next_step="grafana_image_disposition.py full raw Trivy plus native/full Grype exact checker")
    except Exception as error:
        result.update(error_type=type(error).__name__)
        if isinstance(error,ValueError):
            result["error"]=str(error)[:240]
    (args.output_dir/"collector.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:result[k] for k in ("operational_success","security_gate_claim","actual_image_id","error_type","error") if k in result}))
    raise SystemExit(0 if result["operational_success"] else 1)


if __name__=="__main__":
    main()
