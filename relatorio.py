"""Relatório semanal de crédito privado.

Uso:
    python relatorio.py semanas/2026-10-16.toml   (padrão: exemplo.toml)

Imprime o texto e salva em saida/relatorio_<semana_fim>.txt.
Calculado sozinho quando ausente no TOML (valor escrito no TOML prevalece):
  - [indices]: CDI no Banco Central (SGS 12); IDA-DI, IDA-IPCA e IMA-B na ANBIMA;
  - [spread_tradicionais]: spread médio das debêntures DI+ do IDA-DI, pelas taxas
    indicativas da ANBIMA, ponderado por valor de mercado.
"""
import functools
import json
import math
import sys
import time
import tomllib
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

IDA_DOWN = "https://www.anbima.com.br/informacoes/ida/IDA_down.asp"

# Padrão da casa: texto da Mesa, na primeira pessoa do plural, sem emojis.
SAUDACAO = "Bom dia,"
FECHAMENTO = "Caso esteja acompanhando algum emissor ou vencimento específico, estamos à disposição para conversar."
ASSINATURA = "Mesa de Crédito BGC"
DISCLAIMER_FINAL = (
    "Os impactos descritos são potenciais; a reação efetiva depende dos preços e da liquidez dos papéis."
)
REF_TRADICIONAIS = "na média ponderada das debêntures DI+ do IDA-DI, pelas taxas indicativas da ANBIMA"
REF_INFRA = ("na média ponderada das debêntures do IDA-IPCA Infraestrutura, pela taxa indicativa da ANBIMA "
             "sobre a NTN-B de referência")
LIMITE_PALAVRAS = 60  # por notícia, sem contar a linha de mercado


def pct(x):
    return f"{x:+.2f}%".replace(".", ",")


def bi(x):
    s = f"{x:.1f}"
    return (s[:-2] if s.endswith(".0") else s).replace(".", ",")


def mi(v):
    return f"R$ {bi(v / 1000)} bi" if v >= 1000 else f"R$ {v:.0f} mi"


def ddmm(d):
    return d.strftime("%d/%m")


def periodo(ini, fim):
    return f"{ini:%d} a {ddmm(fim)}" if ini.month == fim.month else f"{ddmm(ini)} a {ddmm(fim)}"


def lista(itens):
    return itens[0] if len(itens) == 1 else ", ".join(itens[:-1]) + " e " + itens[-1]


def baixar(url, dados=None):
    corpo = urllib.parse.urlencode(dados).encode() if dados else None
    req = urllib.request.Request(url, data=corpo, headers={"User-Agent": "Mozilla/5.0"})
    for tentativa in range(4):  # a API do Banco Central oscila; tenta de novo antes de desistir
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except OSError:
            if tentativa == 3:
                raise
            time.sleep(20)


def taxas_cdi(ini, fim):
    """[(data, % a.d.)] do Banco Central; as datas servem de calendário de dias úteis."""
    url = (
        "https://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados?formato=json"
        f"&dataInicial={ini - timedelta(days=10):%d/%m/%Y}&dataFinal={fim:%d/%m/%Y}"
    )
    return [(datetime.strptime(x["data"], "%d/%m/%Y").date(), float(x["valor"])) for x in json.loads(baixar(url))]


def janela(taxas, ini, fim):
    """Fechamentos de referência: último dia útil antes de `ini` e último até `fim`."""
    dias = [d for d, _ in taxas]
    antes, ate = [d for d in dias if d < ini], [d for d in dias if ini <= d <= fim]
    if not antes or not ate:
        raise SystemExit(f"Banco Central sem CDI para {periodo(ini, fim)}; preencha [indices] no TOML.")
    return antes[-1], ate[-1]


def cdi_semana(taxas, ref0, ref1):
    """A taxa do dia d rende de d ao dia útil seguinte: entram as de ref0 até a véspera de ref1."""
    return (math.prod(1 + t / 100 for d, t in taxas if ref0 <= d < ref1) - 1) * 100


def num_br(s):
    return float(s.replace(".", "").replace(",", "."))


def numeros_anbima(txt, br):
    """{nome do índice: número-índice} das linhas do CSV da ANBIMA com data `br`."""
    linhas = (l.split(";") for l in txt.splitlines())
    return {c[0]: num_br(c[2]) for c in linhas if len(c) > 2 and c[1] == br}


def anbima(d):
    br = f"{d:%d/%m/%Y}"
    ida = baixar(IDA_DOWN, {
        "tipo": "qr", "indice": "GERAL", "escolha": "2", "Idioma": "PT", "saida": "csv",
        "DataIniD": br, "DataIniM": f"{d:%m/%Y}"})
    ima = baixar("https://www.anbima.com.br/informacoes/ima/ima-sh-down.asp", {
        "Tipo": "", "DataRef": "", "Pai": "ima", "escolha": "2", "Idioma": "PT", "saida": "csv",
        "Dt_Ref_Ver": f"{d:%Y%m%d}", "Dt_Ref": br})
    v = numeros_anbima((ida + b"\n" + ima).decode("latin-1"), br)
    # Coluna "Yield" do IMA: taxa média das NTN-B da carteira, usada na leitura dos índices.
    yld = {c[0]: num_br(c[16]) for c in (l.split(";") for l in ima.decode("latin-1").splitlines())
           if len(c) > 16 and c[0] == "IMA-B" and c[1] == br}  # outros IMAs têm "--" nessa coluna
    try:
        return {"ida_di": v["IDA - DI"], "ida_ipca": v["IDA - IPCA"], "ima_b": v["IMA-B"], "yield_ima_b": yld["IMA-B"]}
    except KeyError as e:
        raise SystemExit(f"ANBIMA sem {e} em {br}; preencha [indices] no TOML.")


@functools.cache
def referencias(ini, fim):
    """(taxas do CDI, fechamento anterior a `ini`, último fechamento até `fim`)."""
    taxas = taxas_cdi(ini, fim)
    ref0, ref1 = janela(taxas, ini, fim)
    if ref1 != fim:
        print(f"[aviso] semana fecha em {ref1:%d/%m} (sem CDI de {fim:%d/%m}: feriado ou não publicado)",
              file=sys.stderr)
    return taxas, ref0, ref1


def indices_semana(ini, fim):
    taxas, ref0, ref1 = referencias(ini, fim)
    a, b = anbima(ref0), anbima(ref1)
    return {"cdi": cdi_semana(taxas, ref0, ref1), **{k: (b[k] / a[k] - 1) * 100 for k in ("ida_di", "ida_ipca", "ima_b")},
            "ntnb_bps": round((b["yield_ima_b"] - a["yield_ima_b"]) * 100), "janela": [ref0, ref1]}


@functools.cache
def carteira(d, indice):
    """{ticker: (indexador, valor de mercado)} da carteira teórica do IDA `indice` vigente em `d`."""
    txt = baixar(IDA_DOWN, {"tipo": "ct", "indice": indice, "escolha": "2", "Idioma": "PT", "saida": "csv",
                            "DataIniD": f"{d:%d/%m/%Y}", "DataIniM": f"{d:%m/%Y}"}).decode("latin-1")
    linhas = (l.split(";") for l in txt.splitlines()[2:])
    return {c[0]: (c[4], num_br(c[5]) * num_br(c[6])) for c in linhas if len(c) > 6}


@functools.cache
def taxas_anbima(d):
    """{ticker: (remuneração, taxa indicativa %, NTN-B de referência)}: arquivo diário de debêntures da ANBIMA."""
    txt = baixar(f"https://www.anbima.com.br/informacoes/merc-sec-debentures/arqs/db{d:%y%m%d}.txt")
    out = {}
    for c in (l.split("@") for l in txt.decode("latin-1").splitlines()):
        if len(c) > 14:
            try:
                out[c[0]] = (c[3], num_br(c[6]), c[14])
            except ValueError:  # cabeçalho ou papel sem taxa indicativa no dia ("--")
                pass
    return out


@functools.cache
def ntnb(d):
    """{vencimento dd/mm/aaaa: taxa indicativa %} das NTN-B, arquivo diário de títulos públicos da ANBIMA."""
    txt = baixar(f"https://www.anbima.com.br/informacoes/merc-sec/arqs/ms{d:%y%m%d}.txt").decode("latin-1")
    return {f"{c[4][6:]}/{c[4][4:6]}/{c[4][:4]}": num_br(c[7])
            for c in (l.split("@") for l in txt.splitlines()) if len(c) > 7 and c[0] == "NTN-B"}


def spreads_di(d):
    """{ticker: spread % sobre o DI} das debêntures DI+."""
    return {t: x for t, (rem, x, _) in taxas_anbima(d).items() if rem.startswith("DI +")}


def spread_over_bps(taxa, b):
    """Spread sobre a NTN-B em bps, na conta do Trades_IPCA da Mesa: ((1 + taxa) / (1 + B) - 1) * 10000."""
    return ((1 + taxa / 100) / (1 + b / 100) - 1) * 10000


def spreads_b(d):
    """{ticker: (spread em bps, ano da NTN-B)} das debêntures IPCA+ sobre a NTN-B de referência da ANBIMA."""
    ref = ntnb(d)
    return {t: (spread_over_bps(x, ref[r]), r[-4:]) for t, (rem, x, r) in taxas_anbima(d).items()
            if rem.startswith("IPCA +") and r in ref}


def spread_medio(pesos, a, b):
    """(nível em `b`, variação de `a` para `b`) em bps, ponderados por `pesos`.

    Só entram papéis cotados nas duas datas: entrada e saída de papéis não vira movimento de spread."""
    comuns = [t for t in pesos if t in a and t in b]
    if not comuns:
        raise SystemExit("ANBIMA sem taxas indicativas comuns às duas datas; preencha o spread no TOML.")
    soma = sum(pesos[t] for t in comuns)
    m0, m1 = (sum(pesos[t] * s[t] for t in comuns) / soma * 100 for s in (a, b))
    return round(m1), round(m1 - m0)


def spread_tradicionais(ini, fim):
    _, ref0, ref1 = referencias(ini, fim)
    pesos = {t: v for t, (ix, v) in carteira(ref1, "GERAL").items() if ix == "DI"}
    nivel, variacao = spread_medio(pesos, spreads_di(ref0), spreads_di(ref1))
    return {"nivel_bps": nivel, "variacao_bps": variacao, "referencia": REF_TRADICIONAIS}


def spread_infra(ini, fim):
    _, ref0, ref1 = referencias(ini, fim)
    pesos = {t: v for t, (_, v) in carteira(ref1, "IPCA_INFRAESTRUTURA").items()}
    # spread_medio trabalha em %, como nos spreads DI+.
    a, b = ({t: s / 100 for t, (s, _) in spreads_b(d).items()} for d in (ref0, ref1))
    nivel, variacao = spread_medio(pesos, a, b)
    return {"nivel_bps": nivel, "variacao_bps": variacao, "referencia": REF_INFRA}


def spread(sp):
    d = sp["variacao_bps"]
    if d:
        txt = f"{'Abertura' if d > 0 else 'Fechamento'} de aproximadamente {abs(d)} bps, chegando a cerca de"
    else:
        txt = "Estável, em cerca de"
    txt += f" {sp['nivel_bps']} bps, {sp.get('referencia') or 'no comparativo semanal reportado pela ' + sp['fonte']}."
    if "data_idex" in sp:
        txt += f" O gráfico IDEX utilizado tem última referência em {ddmm(sp['data_idex'])}."
    return txt


def movimento(bps):
    return "ficou estável" if bps == 0 else f"{'abriu' if bps > 0 else 'fechou'} {abs(bps)} bps"


def leitura(ix, infra, trad):
    """Liga a rentabilidade dos índices ao movimento de taxa e de spread, e diz a janela de cálculo."""
    partes = []
    if "ntnb_bps" in ix:
        partes.append(f"a taxa das NTN-B {movimento(ix['ntnb_bps'])} (yield do IMA-B)")
    if infra:
        partes.append(f"o spread das incentivadas sobre NTN-B {movimento(infra['variacao_bps'])}")
    if trad:
        partes.append(f"o spread das tradicionais sobre o CDI {movimento(trad['variacao_bps'])}")
    txt = f"Na semana, {lista(partes)}." if partes else ""
    if "janela" in ix:
        txt += f" Fechamento ANBIMA de {ddmm(ix['janela'][0])} a {ddmm(ix['janela'][1])}."
    return txt.strip()


def fmt_taxa(rem, x):
    """Taxa indicativa na convenção do papel: "DI + 1,95%", "IPCA + 7,83%", "105,20% do DI"."""
    if rem.startswith(("DI +", "IPCA +")):
        txt = f"{rem.split(' +')[0]} + {x:.2f}%"
    elif "do DI" in rem:
        txt = f"{x:.2f}% do DI"
    else:
        txt = f"{x:.2f}%"
    return txt.replace(".", ",")


def delta(rem, d):
    if "do DI" in rem:  # papel em % do DI anda em pontos percentuais do DI, não em bps
        return "estável" if round(d, 2) == 0 else f"{d:+.2f}".replace(".", ",") + " p.p."
    b = round(d * 100)
    return "estável" if b == 0 else f"{b:+d} bps"


def volume_txt(v):
    if v is None:  # base B3 incompleta na semana: melhor calar do que dizer "sem negócios"
        return ""
    if not v:
        return "sem negócios na semana"
    return "menos de R$ 1 mi negociado" if v < 1 else f"{mi(v)} negociados"


def taxa_mercado(p):
    """Taxa do papel no texto: IPCA+ sai como spread sobre a NTN-B de referência; os demais, na convenção do papel."""
    if p.get("spread1") is None:
        return fmt_taxa(p["indice"], p["taxa1"])
    s = round(p["spread1"])
    return f"NTN-B {p['ntnb']} {'-' if s < 0 else '+'} {abs(s)} bps"


def variacao_mercado(p):
    """Variação na semana: do spread sobre a NTN-B nos IPCA+, da taxa nos demais; None sem o fechamento anterior."""
    if p.get("spread1") is not None:
        if p.get("spread0") is None:
            return None
        b = round(p["spread1"] - p["spread0"])
        return "estável" if b == 0 else f"{b:+d} bps"
    return None if p.get("taxa0") is None else delta(p["indice"], p["taxa1"] - p["taxa0"])


def linha_mercado(tickers, papeis):
    """Taxa indicativa ANBIMA, variação na semana e volume na B3 de cada ticker citado."""
    partes = []
    for t in tickers:
        p = papeis.get(t, {})
        if p.get("taxa1") is None:
            txt = f"{t} sem taxa indicativa ANBIMA"
        else:
            txt = f"{t} a {taxa_mercado(p)}"
            if (d := variacao_mercado(p)) is not None:
                txt += f" ({d} na semana)"
        vt = volume_txt(p.get("volume_mm"))
        partes.append(f"{txt}, {vt}" if vt else txt)
    return "Mercado: " + "; ".join(partes) + "."


def topico(n, papeis):
    linhas = [n["titulo"], n["texto"].strip()]
    if n.get("tickers") and papeis:
        linhas.append(linha_mercado(n["tickers"], papeis))
    return "\n".join(linhas)


def secao(titulo, blocos):
    """Título em caixa alta junto do primeiro bloco; seção sem conteúdo some."""
    blocos = [x for x in blocos if x]
    return [f"{titulo}\n{blocos[0]}", *blocos[1:]] if blocos else []


def item_emissao(i, papeis):
    extras = [f"rating {i['rating']}"] if i.get("rating") else []
    sec = [f"{t} a {taxa_mercado(papeis[t])}" for t in i.get("tickers", [])
           if papeis.get(t, {}).get("taxa1") is not None]
    if sec:
        extras.append("secundário do emissor: " + ", ".join(sec))
    return (f"• {i['emissor']}: {i['taxa']}, {mi(i['volume_mm'])}, vencimento {i['vencimento']}"
            + "".join(f"; {x}" for x in extras) + ".")


def emissoes(e, papeis):
    nomes = e.get("aguardando_bookbuilding")
    frase = f"{lista(nomes)} {'tem' if len(nomes) == 1 else 'têm'} bookbuilding previsto para os próximos dias." if nomes else ""
    itens = [item_emissao(i, papeis) for i in e.get("itens", [])]
    return [itens and "Taxas de emissão na semana (registro na CVM):\n" + "\n".join(itens),
            f"{frase} {e.get('comentario', '')}".strip()]


def volume(nome, atual, anterior):
    ante = f", ante R$ {bi(anterior)} bi na semana anterior" if anterior is not None else ""
    return f"{nome}: R$ {bi(atual)} bi{ante}."


def render(s, mercado=None):
    """Texto do relatório. `mercado`: volumes e papéis da semana gravados pelo coletar.py."""
    ini, fim = s["semana_inicio"], s["semana_fim"]
    papeis = (mercado or {}).get("papeis", {})
    ix, v = s.get("indices", {}), s.get("volumes") or (mercado or {}).get("volumes")
    if not {"ida_di", "cdi", "ida_ipca", "ima_b"} <= ix.keys():
        ix = {**indices_semana(ini, fim), **ix}
    infra = s.get("spread_infra") or spread_infra(ini, fim)
    trad = s.get("spread_tradicionais") or spread_tradicionais(ini, fim)
    c = s.get("credito_bancario") or {}

    b = [  # blocos separados por linha em branco
        s.get("saudacao", SAUDACAO),
        f"Segue nosso resumo semanal de crédito privado, referente à semana de {periodo(ini, fim)}, "
        "com mercado, notícias corporativas, emissões e crédito bancário.",
        *secao("DESTAQUES", ["\n".join(f"• {d}" for d in s.get("destaques", []))]),
        *secao("ÍNDICES", [f"IDA-DI: {pct(ix['ida_di'])}, contra CDI: {pct(ix['cdi'])}.\n"
                           f"IDA-IPCA: {pct(ix['ida_ipca'])}, contra IMA-B: {pct(ix['ima_b'])}.",
                           leitura(ix, infra, trad)]),
        *secao("SPREADS", [f"Infraestrutura (incentivadas), sobre NTN-B\n{spread(infra)}",
                           f"Tradicionais (DI+), sobre o CDI\n{spread(trad)}"]),
        *secao("VOLUME NEGOCIADO NO SECUNDÁRIO", [
            v and volume("Incentivadas", v["incentivadas"], v.get("incentivadas_ant")) + "\n"
            + volume("Não incentivadas", v["nao_incentivadas"], v.get("nao_incentivadas_ant")),
            v and v.get("comentario")]),
        *secao("NOTÍCIAS CORPORATIVAS", [topico(n, papeis) for n in s.get("noticias", [])]),
        *secao("EMISSÕES CORPORATIVAS", emissoes(s["emissoes"], papeis) if "emissoes" in s else []),
        *secao("CRÉDITO BANCÁRIO", ["\n".join(f"• {r}" for r in c.get("ratings", [])), c.get("comentario")]),
        *secao("RADAR", [topico(n, papeis) for n in s.get("radar", [])]),
        DISCLAIMER_FINAL,
        s.get("fechamento", FECHAMENTO),
        f"Abraço,\n{s.get('assinatura', ASSINATURA)}",
        f"Fontes: {lista(s['fontes'])}.",
    ]
    return "\n\n".join(x for x in b if x) + "\n"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    arq = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("exemplo.toml")
    s = tomllib.loads(arq.read_text(encoding="utf-8"))
    m = arq.parent / f"{s['semana_fim']}_mercado.json"
    txt = render(s, json.loads(m.read_text(encoding="utf-8")) if m.exists() else None)
    for n in s.get("noticias", []):
        if (k := len(n["texto"].split())) > LIMITE_PALAVRAS:
            print(f"[aviso] notícia {n['titulo']}: {k} palavras, limite {LIMITE_PALAVRAS}", file=sys.stderr)
    saida = Path(__file__).with_name("saida") / f"relatorio_{s['semana_fim']}.txt"
    saida.parent.mkdir(exist_ok=True)
    saida.write_text(txt, encoding="utf-8")
    print(txt)
    print(f"[salvo em {saida}]", file=sys.stderr)


if __name__ == "__main__":
    main()
