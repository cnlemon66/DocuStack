"""设置 DeepSeek API Key 到 config.json"""
import json
import sys

if len(sys.argv) < 2:
    print("用法: python set_key.py YOUR_API_KEY")
    sys.exit(1)

api_key = sys.argv[1].strip()

with open("config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

if "llm" not in config:
    config["llm"] = {}

config["llm"]["api_key"] = api_key

with open("config.json", "w", encoding="utf-8") as f:
    json.dump(config, f, ensure_ascii=False, indent=2)

print("OK")
