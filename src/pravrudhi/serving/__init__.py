"""SPIKE package (throwaway-until-reviewed): local model-serving shims for the product surface.

Today's only member, `nyaya_local_shim`, is a minimal FastAPI server matching the exact wire contract
`pravrudhi.models.openai_compat.ChatClient` already expects -- so a locally-served checkpoint (a P2b
Qwen2.5-3B + LoRA/merged artifact, run in-process via `transformers`, not GGUF) can register as a
`panel.Vendor(interface="openai_compat", base_url="http://127.0.0.1:<port>/v1")` with zero new interface
code in `panel.py`. See the module docstring for the proven round-trip and what is still open.
"""
