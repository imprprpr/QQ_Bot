"""Apply the narrowly scoped Chinese empty-final fix to the pinned Genie source."""
from pathlib import Path
import sys

root = Path(sys.argv[1])
path = root / "src/genie_tts/G2P/Chinese/ToneSandhi.py"
text = path.read_text(encoding="utf-8")
changes = {
    'and sub_finals_list[i - 1][-1][-1] == "3"':
        'and sub_finals_list[i - 1] and sub_finals_list[i - 1][-1].endswith("3")',
    'and sub_finals_list[i][0][-1] == "3"':
        'and sub_finals_list[i] and sub_finals_list[i][0].endswith("3")',
}
for before, after in changes.items():
    if before in text:
        assert text.count(before) == 1, "Unexpected upstream source layout"
        text = text.replace(before, after)
    elif after not in text:
        raise RuntimeError("Unsupported upstream version; inspect before patching")
backup = path.with_suffix(".py.anima-original")
if not backup.exists():
    backup.write_bytes(path.read_bytes())
path.write_text(text, encoding="utf-8")
print("Chinese empty-final fix applied")
