"""
Testa a API do Ygapó e o antifraude automaticamente.

COMO USAR
1. Deixe a API rodando em outra janela (SEM o modo demonstração).
2. Salve este arquivo na mesma pasta da API.
3. Em um prompt aberto nessa pasta, rode:  python testar_api.py

Não precisa instalar nada além do Python.
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://localhost:8000"

# evita erro de acentos no prompt do Windows
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def chamar(metodo, caminho, corpo=None):
    dados = json.dumps(corpo).encode("utf-8") if corpo is not None else None
    req = urllib.request.Request(
        BASE + caminho, data=dados, method=metodo,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}
    except Exception as e:
        return None, {"erro": str(e)}


def descarte(**mudancas):
    """Monta um descarte válido (timestamp de agora) e aplica as mudanças."""
    corpo = {
        "usuario_id": 1,
        "ecoponto_id": 1,
        "material_detectado": "pet_bottle",
        "confianca": 0.9,
        "latitude": -3.1363,
        "longitude": -60.0230,
        "timestamp": int(time.time()),
        "mock_location": False,
    }
    corpo.update(mudancas)
    return chamar("POST", "/api/descarte/validar", corpo)


def resumo(codigo, corpo):
    if isinstance(corpo, dict):
        if "detail" in corpo:
            return str(corpo["detail"])
        if "recurso_gerado" in corpo:
            return (f"{corpo['recurso_gerado']} | +{corpo['eco_moedas_ganhas']} EcoMoedas"
                    f" | XP total {corpo['xp_total']} | nivel {corpo['nivel']}")
        if "nome" in corpo:
            return corpo["nome"]
    return str(corpo)


testes = [
    ("QR Code valido (YGAPO-001)",
     lambda: chamar("GET", "/api/ecoponto/YGAPO-001"), 200),
    ("QR Code invalido (ABC)",
     lambda: chamar("GET", "/api/ecoponto/ABC"), 404),
    ("1o descarte valido (PET)",
     lambda: descarte(), 200),
    ("Descarte repetido no mesmo ponto (intervalo de 2 min)",
     lambda: descarte(), 429),
    ("Longe do ecoponto (latitude -3.2)",
     lambda: descarte(latitude=-3.2), 403),
    ("Requisicao antiga (timestamp 1)",
     lambda: descarte(timestamp=1), 400),
    ("IA com pouca certeza (confianca 0.5)",
     lambda: descarte(confianca=0.5), 422),
    ("GPS falso (mock_location true)",
     lambda: descarte(mock_location=True), 403),
    ("Material desconhecido (banana)",
     lambda: descarte(material_detectado="banana"), 422),
]

print("=" * 70)
print("TESTE DA API YGAPO  -  " + BASE)
print("=" * 70)

acertos = 0
for nome, acao, esperado in testes:
    codigo, corpo = acao()
    if codigo is None:
        print("\nNao consegui conectar na API. Ela esta rodando?")
        print("Detalhe:", corpo.get("erro"))
        sys.exit(1)
    ok = codigo == esperado
    acertos += ok
    print(f"\n[{'OK' if ok else 'DIFERENTE'}] {nome}")
    print(f"     esperado: {esperado}   recebido: {codigo}")
    print(f"     resposta: {resumo(codigo, corpo)}")

print("\n" + "=" * 70)
print(f"RESULTADO: {acertos} de {len(testes)} testes como esperado")
print("=" * 70)
if acertos != len(testes):
    print("\nSe o 1o descarte nao deu 200 (e sim 429), houve outro descarte neste")
    print("ponto nos ultimos 2 minutos. Espere 2 minutos ou apague o ygapo.db,")
    print("reinicie a API e rode o teste de novo.")
    print("Se a API estiver com o modo demonstracao ligado, varios testes")
    print("vao dar diferente do esperado: reinicie a API sem o modo demo.")
