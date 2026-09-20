"""Read-only, readable procedure listing for the source Scratch project."""
import argparse
import json
from pathlib import Path
from zipfile import ZipFile


def project():
    with ZipFile(next(Path(__file__).resolve().parents[1].glob("*.sb3"))) as z:
        return json.loads(z.read("project.json"))


def describe(sprite, procedure):
    target = next(t for t in project()["targets"] if t["name"] == sprite)
    blocks = target["blocks"]

    def expr(value):
        if isinstance(value, list):
            return str(value[1]) if value[0] in (12, 13) else repr(value[1])
        if value not in blocks:
            return str(value)
        b = blocks[value]
        if not isinstance(b, dict):
            return str(b)
        op = b.get("mutation", {}).get("proccode", b["opcode"])
        fields = [str(v[0]) for v in b.get("fields", {}).values()]
        args = [f"{k}={expr(v[1])}" for k, v in b.get("inputs", {}).items() if not k.startswith("SUBSTACK")]
        return f"{op}({', '.join(fields + args)})"

    def walk(bid, level=0):
        while bid:
            b = blocks[bid]
            print("  " * level + expr(bid))
            for key, value in b.get("inputs", {}).items():
                if key.startswith("SUBSTACK") and isinstance(value[1], str):
                    print("  " * (level + 1) + key + ":")
                    walk(value[1], level + 2)
            bid = b.get("next")

    for b in blocks.values():
        if procedure == '@clones' and isinstance(b, dict) and b.get('opcode') == 'control_start_as_clone':
            print('\n### clone dispatch')
            walk(b.get('next'))
        if procedure == '@events' and isinstance(b, dict) and b.get('opcode') == 'event_whenbroadcastreceived':
            print('\n### event', expr(next(k for k, v in blocks.items() if v is b)))
            walk(b.get('next'))
        if isinstance(b, dict) and b.get("opcode") == "procedures_definition":
            name = blocks[b["inputs"]["custom_block"][1]]["mutation"]["proccode"]
            if procedure.lower() in name.lower():
                print("\n###", name)
                walk(b.get("next"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("procedure")
    parser.add_argument("--sprite", default="uno")
    args = parser.parse_args()
    describe(args.sprite, args.procedure)
