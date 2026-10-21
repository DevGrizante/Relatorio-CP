"""Rode: python test_relatorio.py"""
import tomllib
from datetime import date, timedelta
from pathlib import Path

from coletar import (completa, maiores_deltas, norm, origem_indexador, por_emissor, rotina, taxa_negociada, tickers,
                     ultima_sexta, volume_por_emissor, volumes)
from relatorio import (bi, cdi_semana, contexto, item_emissao, janela, leitura, linha_mercado, lista, mi,
                       numeros_anbima, periodo, render, spread_medio, spread_over_bps, validar)

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

# Volume da semana por emissor: soma por CNPJ (via ticker) e acha o emissor da CVM pelo CNPJ formatado.
negocios = [("MILLS S/A", "MILS18", 10.0, 0.93, date(2026, 10, 14)), ("MILLS S/A", "MILSA1", 5.0, None, date(2026, 10, 15))]
vol = volume_por_emissor(negocios, {"MILS18": "27.093.558/0001-15", "MILSA1": "27093558000115"})
assert por_emissor(vol, "27.093.558/0001-15", "Outro nome") == 15.0
assert por_emissor(vol, "", "Mills S.A.") == 15.0 and por_emissor(vol, "", "Vale") is None

# Taxa negociada: média ponderada só dos negócios com taxa (a partir de R$ 1 mi, sem passagens).
assert taxa_negociada([("X", "A", 3e6, 1.0, None), ("X", "A", 1e6, 2.0, None), ("X", "A", 9e6, None, None)], {}) == {"A": 1.25}

# Maior variação por emissor: negociada e indicativa; IPCA+ pelo spread, não pela taxa (alta da NTN-B não é crédito).
debs = [{"ticker": "AAA11", "cnpj": "1", "emissor": "Alfa"}, {"ticker": "AAA21", "cnpj": "1", "emissor": "Alfa"}]
pp = {"AAA11": {"indice": "DI + 1%", "taxa0": 1.00, "taxa1": 1.10, "neg0": 1.0, "neg1": 1.03},
      "AAA21": {"indice": "IPCA + 6%", "taxa0": 7.0, "taxa1": 7.5, "spread0": 80.0, "spread1": 86.0, "ntnb": "2030"}}
dneg, dind = maiores_deltas(debs, pp)
assert (por_emissor(dneg, "1", ""), por_emissor(dind, "1", "")) == (3, 10)

# Bloco de mercado: negócio B3 primeiro; indicativa ANBIMA é marcação, com % do par e variação.
papeis = {"MILS18": {"indice": "DI + 2%", "taxa0": 0.9246, "taxa1": 0.9453, "par": 101.96, "volume_mm": 8.39,
                     "neg1": 0.93, "neg0": 0.94},
          "XPTO11": {"indice": "105% do DI", "taxa0": 104.0, "taxa1": 104.5, "volume_mm": 0.0},
          "PEQ11": {"indice": "DI + 1%", "taxa0": None, "taxa1": 1.0, "volume_mm": 3.2},
          "INCOMPL": {"indice": "IPCA + 6%", "taxa0": 7.0, "taxa1": 7.0, "volume_mm": None},
          "APRB18": {"indice": "IPCA + 4,5%", "taxa0": 7.6556, "taxa1": 7.7549, "par": 91.56, "volume_mm": 11.06,
                     "neg1": 119.0, "neg0": None, "spread0": 80.6, "spread1": 84.7, "ntnb": "2029"}}
assert linha_mercado(["MILS18", "XPTO11", "PEQ11", "INCOMPL", "APRB18", "SEMDADO"], papeis) == (
    "Mercado:\n"
    "• MILS18: R$ 8 mi negociados a DI + 0,93% (-1 bps ante a média da semana anterior); "
    "indicativa ANBIMA DI + 0,95%, 101,96% do par, +2 bps na semana.\n"
    "• XPTO11: sem negócios na semana; indicativa ANBIMA 104,50% do DI, +0,50 p.p. na semana.\n"
    "• PEQ11: R$ 3 mi negociados, todos abaixo de R$ 1 mi por negócio; indicativa ANBIMA DI + 1,00%.\n"
    "• INCOMPL: indicativa ANBIMA IPCA + 7,00%, estável na semana.\n"
    "• APRB18: R$ 11 mi negociados a NTN-B 2029 + 119 bps; indicativa ANBIMA NTN-B 2029 + 85 bps, 91,56% do par, "
    "+4 bps na semana.\n"
    "• SEMDADO: sem taxa indicativa ANBIMA nem negócios na B3.")

# Spread sobre NTN-B na conta do Trades_IPCA; emissão IPCA+ nova usa a NTN-B de vencimento mais próximo.
assert round(spread_over_bps(7.7549, 6.85), 1) == 84.7
curva = {"15/08/2030": 6.88, "15/08/2050": 6.90}
assert item_emissao({"emissor": "MEZ", "taxa": "IPCA + 8,10% a.a.", "volume_mm": 334, "vencimento": "07/2050",
                     "tickers": ["APRB18"]}, papeis, curva) == (
    "• MEZ: IPCA + 8,10% a.a. (NTN-B 2050 + 112 bps), R$ 334 mi, vencimento 07/2050; "
    "secundário do emissor (indicativa ANBIMA): APRB18 a NTN-B 2029 + 85 bps.")
assert item_emissao({"emissor": "LM", "taxa": "CDI + 1,80% a.a.", "volume_mm": 750, "vencimento": "10/2030",
                     "rating": "AA(bra)", "tickers": ["MILS18"]}, papeis, curva) == (
    "• LM: CDI + 1,80% a.a., R$ 750 mi, vencimento 10/2030; rating AA(bra); "
    "secundário do emissor (indicativa ANBIMA): MILS18 a DI + 0,95%.")
assert leitura({"ntnb_bps": 18, "janela": [date(2026, 10, 9), date(2026, 10, 16)]},
               {"variacao_bps": 22}, {"variacao_bps": 0}) == (
    "Na semana, a taxa das NTN-B abriu 18 bps (yield do IMA-B), o spread das incentivadas sobre NTN-B abriu "
    "22 bps e o spread das tradicionais sobre o CDI ficou estável. Fechamento ANBIMA de 09/10 a 16/10.")

# Contexto histórico: só aparece quando nenhuma das últimas 8 semanas andou tanto.
semanas = [{"ref1": date(2026, 7, 3) + timedelta(weeks=k), "infra_var": v} for k, v in enumerate([25, 1, 2, 3, 4, 5, 6, 7, 8, 9])]
hoje = date(2026, 10, 16)
assert contexto(semanas, "infra_var", 20, hoje) == "maior abertura semanal desde 03/07/2026"
assert contexto(semanas, "infra_var", 30, hoje) == "maior abertura semanal em 10 semanas"
assert contexto(semanas, "infra_var", 8, hoje) == ""   # semana recente já andou 9
assert contexto(semanas, "infra_var", 3, hoje) == ""   # movimento pequeno
assert contexto(semanas[:5], "infra_var", 30, hoje) == ""  # histórico curto demais

# Verificação: o texto não sai com emoji, singular, fonte sem comentário ou indexador não conferido.
s_ok = {"fontes": ["ANBIMA", "Fitch"], "noticias": [], "emissoes": {"itens": [
    {"emissor": "MEZ", "requerimento": 1, "conferido": "escritura"}]}}
bruto_ok = "# fonte: https://www.fitchratings.com/x\n"
assert validar(s_ok, "Texto da Mesa.", bruto_ok, {"origem_indexador": {"1": "inferido — conferir na escritura"}}) == []
erros = validar({"fontes": ["Fitch", "S&P"], "noticias": [], "emissoes": {"itens": [{"emissor": "MEZ", "requerimento": 1}]}},
                "Seguimos acompanhando. Me chama. 🏥 AGDs da 1ª série da 8ª emissão.", bruto_ok,
                {"origem_indexador": {"1": "inferido — conferir na escritura"}})
assert len(erros) == 6, erros

# Volume só sai com a semana inteira na base; feriado sem base não conta como falta.
uteis = [(date(2026, 10, d), 0.05) for d in (9, 13, 14, 15, 16)]
assert completa([date(2026, 10, 12)], uteis, date(2026, 10, 12), date(2026, 10, 16))
assert not completa([date(2026, 10, 16)], uteis, date(2026, 10, 12), date(2026, 10, 16))
v = volumes([("A", "INC11", 2e9, None, None), ("B", "NAO11", 1e9, None, None)], None,
            [{"ticker": "INC11", "incentivada": True}])
assert (v["incentivadas"], v["nao_incentivadas"], "incentivadas_ant" in v) == (2.0, 1.0, False)

# O exemplo.toml tem que reproduzir exatamente o e-mail de referência.
gerado = render(tomllib.loads((aqui / "exemplo.toml").read_text(encoding="utf-8")))
assert gerado == (aqui / "exemplo.txt").read_text(encoding="utf-8"), gerado

print("ok")
