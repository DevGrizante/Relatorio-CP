"""Rode: python test_relatorio.py"""
import tomllib
from datetime import date
from pathlib import Path

from coletar import (completa, norm, origem_indexador, por_emissor, rotina, tickers, ultima_sexta, volume_por_emissor,
                     volumes)
from relatorio import (bi, cdi_semana, item_emissao, janela, leitura, linha_mercado, lista, mi, numeros_anbima, periodo,
                       render, spread_medio, spread_over_bps)

aqui = Path(__file__).parent

assert periodo(date(2026, 10, 5), date(2026, 10, 9)) == "05 a 09/10"
assert periodo(date(2026, 9, 28), date(2026, 10, 2)) == "28/09 a 02/10"
assert lista(["A"]) == "A" and lista(["A", "B", "C"]) == "A, B e C"
assert bi(10) == "10" and bi(13.9) == "13,9"
assert mi(750) == "R$ 750 mi" and mi(1500) == "R$ 1,5 bi"

# Semana do dossiê: a última segunda-a-sexta já encerrada.
assert ultima_sexta(date(2026, 10, 26)) == date(2026, 10, 23)  # segunda
assert ultima_sexta(date(2026, 10, 20)) == date(2026, 10, 16)  # terça
assert ultima_sexta(date(2026, 10, 23)) == date(2026, 10, 16)  # sexta: semana ainda aberta
assert ultima_sexta(date(2026, 10, 25)) == date(2026, 10, 23)  # domingo

# ANBIMA e CVM grafam a mesma razão social de jeitos diferentes.
assert norm("CEMIG DISTRIBUICAO S/A") == norm("Cemig Distribuição S.A.") == "CEMIG DISTRIBUICAO"
assert rotina("Amortização//Pagamento de juros/remuneração") and not rotina("Resgate Antecipado 5 Emissão Debs")

# De-para de tickers: casa por nome normalizado; CNPJ vazio (export local da ANBIMA) não casa com tudo.
deb = [{"emissor": "AUTOPISTA REGIS BITTENCOURT S/A", "cnpj": "", "ticker": "APRB28", "emissao": "8",
        "serie": "2", "vencimento": "15/06/2027", "indice": "DI"}]
assert tickers(deb, {""}, set(), {}) == "_nenhum item_"
assert "APRB28" in tickers(deb, set(), {norm("AUTOPISTA REGIS BITTENCOURT SA")}, {})

# Volume da semana por emissor: soma por CNPJ (via ticker) e acha o emissor da CVM pelo CNPJ formatado.
vol = volume_por_emissor([("MILLS S/A", "MILS18", 10.0), ("MILLS S/A", "MILSA1", 5.0)],
                         {"MILS18": "27.093.558/0001-15", "MILSA1": "27093558000115"})
assert por_emissor(vol, "27.093.558/0001-15", "Outro nome") == 15.0
assert por_emissor(vol, "", "Mills S.A.") == 15.0 and por_emissor(vol, "", "Vale") is None
# Indexador da banda ≤ 6% (SABESP 28127) não pode sair como declarado; painel sem a coluna também não.
assert origem_indexador({"indexador": "CDI / DI", "indexador_inferido": True}) == "inferido — conferir na escritura"
assert origem_indexador({"indexador": "CDI / DI", "indexador_inferido": False}) == "declarado na taxa"
assert origem_indexador({"indexador": "CDI / DI"}) == "não informado pelo painel"
assert origem_indexador({"indexador": "Não Informado", "indexador_inferido": False}) == "—"

# Semana com feriado na segunda (12/10): base é o fechamento de sexta 09/10.
taxas = [(date(2026, 10, 8), 9.0), (date(2026, 10, 9), 1.0), (date(2026, 10, 13), 1.0), (date(2026, 10, 16), 9.0)]
ref0, ref1 = janela(taxas, date(2026, 10, 12), date(2026, 10, 16))
assert (ref0, ref1) == (date(2026, 10, 9), date(2026, 10, 16))
# CDI: entra a taxa de ref0 (09/10); a de ref1 (16/10) rende para a semana seguinte.
assert abs(cdi_semana(taxas, ref0, ref1) - 2.01) < 1e-9

# Spread médio ponderado: papel fora dos pesos (C) não entra; variação compara o mesmo conjunto.
assert spread_medio({"A": 1, "B": 3}, {"A": 1.0, "B": 2.0}, {"A": 1.2, "B": 2.0, "C": 9.0}) == (180, 5)

csv = "Índices;Data;Número Índice\nIDA - DI;09/10/2026;4.971,463160\nIDA - DI;08/10/2026;1,0"
assert numeros_anbima(csv, "09/10/2026") == {"IDA - DI": 4971.46316}

# Linha de mercado: taxa na convenção do papel, variação em bps (p.p. para % do DI), volume B3.
papeis = {"MILS18": {"indice": "DI + 2%", "taxa0": 0.9246, "taxa1": 0.9453, "volume_mm": 8.39},
          "XPTO11": {"indice": "105% do DI", "taxa0": 104.0, "taxa1": 104.5, "volume_mm": 0.0},
          "INCOMPL": {"indice": "IPCA + 6%", "taxa0": 7.0, "taxa1": 7.0, "volume_mm": None}}
assert linha_mercado(["MILS18", "XPTO11", "INCOMPL", "SEMDADO"], papeis) == (
    "Mercado: MILS18 a DI + 0,95% (+2 bps na semana), R$ 8 mi negociados; "
    "XPTO11 a 104,50% do DI (+0,50 p.p. na semana), sem negócios na semana; "
    "INCOMPL a IPCA + 7,00% (estável na semana); SEMDADO sem taxa indicativa ANBIMA.")
# IPCA+ com spread gravado pelo coletar.py: sai como spread sobre a NTN-B, na conta do Trades_IPCA.
assert round(spread_over_bps(7.7549, 6.85), 1) == 84.7
papeis["APRB18"] = {"indice": "IPCA + 4,5%", "taxa0": 7.6556, "taxa1": 7.7549, "volume_mm": 11.06,
                    "spread0": 80.6, "spread1": 84.7, "ntnb": "2032"}
papeis["NOVO11"] = {"indice": "IPCA + 6%", "taxa0": None, "taxa1": 6.5, "volume_mm": 0.0,
                    "spread0": None, "spread1": -3.2, "ntnb": "2030"}
assert linha_mercado(["APRB18", "NOVO11"], papeis) == (
    "Mercado: APRB18 a NTN-B 2032 + 85 bps (+4 bps na semana), R$ 11 mi negociados; "
    "NOVO11 a NTN-B 2030 - 3 bps, sem negócios na semana.")
assert item_emissao({"emissor": "MEZ", "taxa": "IPCA + 8,10% a.a.", "volume_mm": 334, "vencimento": "07/2050",
                     "tickers": ["APRB18"]}, papeis) == (
    "• MEZ: IPCA + 8,10% a.a., R$ 334 mi, vencimento 07/2050; secundário do emissor: APRB18 a NTN-B 2032 + 85 bps.")
assert item_emissao({"emissor": "LM", "taxa": "CDI + 1,80% a.a.", "volume_mm": 750, "vencimento": "10/2030",
                     "rating": "AA(bra)", "tickers": ["MILS18"]}, papeis) == (
    "• LM: CDI + 1,80% a.a., R$ 750 mi, vencimento 10/2030; rating AA(bra); secundário do emissor: MILS18 a DI + 0,95%.")
assert leitura({"ntnb_bps": 18, "janela": [date(2026, 10, 9), date(2026, 10, 16)]},
               {"variacao_bps": 22}, {"variacao_bps": 0}) == (
    "Na semana, a taxa das NTN-B abriu 18 bps (yield do IMA-B), o spread das incentivadas sobre NTN-B abriu "
    "22 bps e o spread das tradicionais sobre o CDI ficou estável. Fechamento ANBIMA de 09/10 a 16/10.")

# Volume só sai com a semana inteira na base; feriado sem base não conta como falta.
uteis = [(date(2026, 10, d), 0.05) for d in (9, 13, 14, 15, 16)]
assert completa([date(2026, 10, 12)], uteis, date(2026, 10, 12), date(2026, 10, 16))
assert not completa([date(2026, 10, 16)], uteis, date(2026, 10, 12), date(2026, 10, 16))
v = volumes([("A", "INC11", 2e9), ("B", "NAO11", 1e9)], None, [{"ticker": "INC11", "incentivada": True}])
assert (v["incentivadas"], v["nao_incentivadas"], "incentivadas_ant" in v) == (2.0, 1.0, False)

# O exemplo.toml tem que reproduzir exatamente o e-mail de referência.
gerado = render(tomllib.loads((aqui / "exemplo.toml").read_text(encoding="utf-8")))
assert gerado == (aqui / "exemplo.txt").read_text(encoding="utf-8"), gerado

print("ok")
