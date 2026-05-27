"""
Testa rapidamente qual provedor de IA está funcionando.
Rode: python test_api.py
"""
import sys
import os
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Carrega .env
from config import GEMINI_API_KEY, GROQ_API_KEY, MISTRAL_API_KEY, get_default_provider
import requests

def test_gemini(key):
    if not key:
        return False, "chave nao configurada"
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"gemini-2.0-flash:generateContent?key={key}")
    try:
        r = requests.post(url, json={
            "contents": [{"parts": [{"text": "Diga apenas: ok"}]}],
            "generationConfig": {"maxOutputTokens": 5}
        }, timeout=15)
        if r.ok:
            return True, r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        return False, f"HTTP {r.status_code}: {r.json().get('error',{}).get('message','')}"
    except Exception as e:
        return False, str(e)

def test_groq(key):
    if not key:
        return False, "chave nao configurada"
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "llama-3.3-70b-versatile",
                  "messages": [{"role": "user", "content": "Diga apenas: ok"}],
                  "max_tokens": 5},
            timeout=15)
        if r.ok:
            return True, r.json()["choices"][0]["message"]["content"].strip()
        return False, f"HTTP {r.status_code}: {r.json().get('error',{}).get('message','')}"
    except Exception as e:
        return False, str(e)

def test_mistral(key):
    if not key:
        return False, "chave nao configurada"
    try:
        r = requests.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "mistral-small-latest",
                  "messages": [{"role": "user", "content": "Diga apenas: ok"}],
                  "max_tokens": 5},
            timeout=15)
        if r.ok:
            return True, r.json()["choices"][0]["message"]["content"].strip()
        return False, f"HTTP {r.status_code}: {r.json().get('error',{}).get('message','')}"
    except Exception as e:
        return False, str(e)

print("\nTestando provedores de IA...\n")

ok, msg = test_gemini(GEMINI_API_KEY)
print(f"  Gemini  : {'OK - ' + msg if ok else 'FALHOU - ' + msg}")

ok, msg = test_groq(GROQ_API_KEY)
print(f"  Groq    : {'OK - ' + msg if ok else 'FALHOU - ' + msg}")

ok, msg = test_mistral(MISTRAL_API_KEY)
print(f"  Mistral : {'OK - ' + msg if ok else 'FALHOU - ' + msg}")

provider, key = get_default_provider()
print(f"\n  Provedor ativo: {provider or 'nenhum'}")
if not provider:
    print("  Adicione uma chave no arquivo .env para continuar.")
