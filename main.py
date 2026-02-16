from ollama import Client
import re
import sys

from pathlib import Path


# CHANGED: Validate that the model output is ONLY a single file protocol block for out/script.md.
def is_valid_single_script_md(text: str) -> bool:
    s = text.strip()
    return (
        s.startswith("@@@FILE:out/script.md@@@")
        and (s.endswith("@@@END@@@") or s.endswith("@@@END@@"))
    )


def apply_llm_output(text, root="."):
    root = Path(root)
    # CHANGED: Strip accidental markdown code fences from model output.
    text = re.sub(r"```[a-zA-Z0-9_-]*\n", "", text)
    text = text.replace("```", "")
    # CHANGED: Normalize root path.
    root = root.expanduser().resolve()

    # --- DELETE ---
    for path in re.findall(r'@@@DELETE:(.*?)@@@', text):
        p = root / path.strip()
        if p.exists():
            print("DELETE", p)
            p.unlink()

    # --- FILE ---
    for path, content in re.findall(r'@@@FILE:(.*?)@@@\s*(.*?)\s*@@@END@@@?', text, re.S):
        p = root / path.strip()
        p.parent.mkdir(parents=True, exist_ok=True)
        print("WRITE", p)
        p.write_text(content, encoding="utf-8")

    # --- PATCH ---
    for path, body in re.findall(r'@@@PATCH:(.*?)@@@\n(.*?)\n@@@END@@@', text, re.S):
        p = root / path.strip()
        if not p.exists():
            print("PATCH FAIL (missing file)", p)
            continue

        src = p.read_text(encoding="utf-8")

        for find, repl in re.findall(r'@@@FIND@@@\n(.*?)\n@@@REPLACE@@@\n(.*?)\n', body, re.S):
            if find not in src:
                print("PATCH FAIL (pattern not found)", p, "::", find[:60].replace("\n", "\\n"))  # CHANGED
                continue
            src = src.replace(find, repl)

        print("PATCH", p)
        p.write_text(src, encoding="utf-8")
        
        
# CHANGED: Use a task prompt that returns only protocol blocks, then apply them to disk.
client = Client(host="http://192.168.178.28:11434")

input_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("script.gd")
input_path = input_path.expanduser().resolve()

if not input_path.exists():
    raise FileNotFoundError(f"Input file not found: {input_path}")

source_code = input_path.read_text(encoding="utf-8")

# CHANGED: Optional prompt and rules files (KISS)
def _read_optional(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path and path.exists() else ""

prompt_path = Path(sys.argv[2]).expanduser().resolve() if len(sys.argv) > 2 else None
rules_path  = Path(sys.argv[3]).expanduser().resolve() if len(sys.argv) > 3 else None

prompt_text = _read_optional(prompt_path) if prompt_path else ""
rules_text  = _read_optional(rules_path) if rules_path else ""

# CHANGED: Compose prompt from rules + prompt file + input file
prompt = f"""
{rules_text}

{prompt_text}

EINGABEDATEI: {input_path.name}

```gdscript
{source_code}
```
""".strip()

# CHANGED: Use a strict system message + retry loop to enforce the protocol.
system = """
Du bist ein deterministischer Dateigenerator.

REGELN (müssen eingehalten werden):
- Gib AUSSCHLIESSLICH das Datei-Protokoll zurück, KEIN Text außerhalb der Marker.
- KEINE Code-Fences (keine ```json / ```plaintext / ``` usw.).
- KEIN JSON, KEINE YAML, KEIN XML.
- Erzeuge GENAU EINE Datei: out/script.md
- Der Inhalt der Datei `out/script.md` MUSS Markdown sein.
- Der Output MUSS mit '@@@FILE:out/script.md@@@' beginnen und mit '@@@END@@@' enden.
""".strip()

llm_output = ""
user_task = prompt

for attempt in range(3):
    r = client.chat(
        model="qwen2.5-coder:7b",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_task},
        ],
    )

    llm_output = r["message"]["content"]
    print(llm_output)

    if is_valid_single_script_md(llm_output):
        break

    # Ask to repair ONLY the format and keep content.
    user_task = (
        "FORMAT-REPAIR: Deine letzte Antwort war UNGÜLTIG, weil sie nicht dem Datei-Protokoll entsprach.\n"
        "Korrigiere NUR das FORMAT und gib NUR die Datei out/script.md im Protokoll zurück.\n"
        "BEGINNE exakt mit: @@@FILE:out/script.md@@@\n"
        "ENDE exakt mit: @@@END@@@\n"
        "Kein Text außerhalb der Marker. Keine Code-Fences. Kein JSON.\n\n"
        "Ungültige Antwort (nur zur Referenz):\n" + llm_output
    )

if not is_valid_single_script_md(llm_output):
    Path("out").mkdir(parents=True, exist_ok=True)
    Path("out/llm_invalid_output.txt").write_text(llm_output, encoding="utf-8")
    raise RuntimeError("LLM ignored the protocol after 3 attempts; saved raw output to out/llm_invalid_output.txt")

# CHANGED: Apply the model output to the local filesystem.
apply_llm_output(llm_output, root=".")