"""Relatório semanal de crédito privado.

Uso:
    python relatorio.py semanas/2026-10-16.toml   (padrão: exemplo.toml)

Imprime o texto e salva em saida/relatorio_<semana_fim>.txt.
Calculado sozinho quando ausente no TOML (valor escrito no TOML prevalece):
  - [indices]: CDI no Banco Central (SGS 12); IDA-DI, IDA-IPCA e IMA-B na ANBIMA;
  - [spread_tradicionais]: spread médio das debêntures DI+ do IDA-DI, pelas taxas
    indicativas da ANBIMA, ponderado por valor de mercado.
"""
import csv
import functools
import json
import math
import re
import sys
import time
import tomllib
import unicodedata
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
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
METODOLOGIA = ("Metodologia: índices e spreads pelo fechamento ANBIMA, de sexta a sexta; spread infra por composição "
               "sobre a NTN-B de referência da ANBIMA, ((1 + taxa) / (1 + NTN-B) - 1); volume e taxa negociada pelos "
               "negócios registrados na B3, sem operações de passagem; taxa negociada é a média ponderada dos negócios "
               "a partir de R$ 1 mi.")
HISTORICO = Path(__file__).with_name("historico_spreads.csv")
CAMPOS_HISTORICO = ["ref1", "ref0", "infra_nivel", "infra_var", "trad_nivel", "trad_var"]
FONTES_AUTOMATICAS = {"ANBIMA", "B3", "Banco Central", "CVM"}
# Como cada fonte aparece nos comentários "# fonte:" do arquivo da semana (URL ou nome).
FONTE_ALIAS = {"s&p": ("spglobal",), "comunicados": ("radcvmgovbr", "comunicado", "fatorelevante"),
               "valoreconomico": ("valor",)}


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
def debentures_anbima(d):
    """{ticker: colunas} do arquivo diário de debêntures da ANBIMA (taxa indicativa, % do par, duration...)."""
    txt = baixar(f"https://www.anbima.com.br/informacoes/merc-sec-debentures/arqs/db{d:%y%m%d}.txt")
    return {c[0]: c for c in (l.split("@") for l in txt.decode("latin-1").splitlines()) if len(c) > 14}


@functools.cache
def taxas_anbima(d):
    """{ticker: (remuneração, taxa indicativa %, NTN-B de referência)}."""
    out = {}
    for t, c in debentures_anbima(d).items():
        try:
            out[t] = (c[3], num_br(c[6]), c[14])
        except ValueError:  # cabeçalho ou papel sem taxa indicativa no dia ("--")
            pass
    return out


def par_duration(d):
    """{ticker: (% do PU par, duration em anos)}: base para dizer quanto um resgate ou um waiver vale."""
    out = {}
    for t, c in debentures_anbima(d).items():
        try:
            out[t] = (num_br(c[11]), num_br(c[12]) / 252)
        except ValueError:
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


def semanas_fechadas(inicio, fim):
    """[(fechamento anterior, fechamento da semana)] de cada semana entre `inicio` e `fim`, pelo calendário do CDI."""
    ultimo = {}
    for d, _ in taxas_cdi(inicio, fim):
        if d >= inicio - timedelta(days=7):
            ultimo[d.isocalendar()[:2]] = d  # último dia útil de cada semana
    f = sorted(ultimo.values())
    return [(a, b) for a, b in zip(f, f[1:]) if b >= inicio]


def ler_historico():
    if not HISTORICO.exists():
        return []
    with open(HISTORICO, encoding="utf-8", newline="") as f:
        return [{k: date.fromisoformat(v) if k.startswith("ref") else int(v) for k, v in r.items()}
                for r in csv.DictReader(f)]


def gravar_historico(linhas):
    unicas = sorted({r["ref1"]: r for r in linhas}.values(), key=lambda r: r["ref1"])
    with open(HISTORICO, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS_HISTORICO)
        w.writeheader()
        w.writerows(unicas)


def linha_historico(ref0, ref1, infra, trad):
    return {"ref1": ref1, "ref0": ref0, "infra_nivel": infra["nivel_bps"], "infra_var": infra["variacao_bps"],
            "trad_nivel": trad["nivel_bps"], "trad_var": trad["variacao_bps"]}


def reconstruir_historico(semanas=52):
    """Recalcula os spreads semanais das últimas `semanas` pelos arquivos públicos da ANBIMA (rodar uma vez)."""
    hoje = date.today()
    linhas = ler_historico()
    feitas = {r["ref1"] for r in linhas}
    for ref0, ref1 in semanas_fechadas(hoje - timedelta(weeks=semanas), hoje):
        if ref1 in feitas or ref1 >= hoje:
            continue
        try:
            pi = {t: v for t, (_, v) in carteira(ref1, "IPCA_INFRAESTRUTURA").items()}
            pd_ = {t: v for t, (ix, v) in carteira(ref1, "GERAL").items() if ix == "DI"}
            a, b = ({t: s / 100 for t, (s, _) in spreads_b(d).items()} for d in (ref0, ref1))
            ni, vi = spread_medio(pi, a, b)
            nt, vt = spread_medio(pd_, spreads_di(ref0), spreads_di(ref1))
        except (OSError, SystemExit) as e:
            print(f"[aviso] semana de {ref1:%d/%m/%Y} sem dados: {e}", file=sys.stderr)
            continue
        linhas.append(linha_historico(ref0, ref1, {"nivel_bps": ni, "variacao_bps": vi},
                                      {"nivel_bps": nt, "variacao_bps": vt}))
        print(f"{ref1:%d/%m/%Y}: infra {ni} ({vi:+d}), tradicionais {nt} ({vt:+d})")
        gravar_historico(linhas)


def contexto(historico, chave, var, ref1, minimo=8):
    """"maior abertura semanal desde dd/mm/aaaa" quando nenhuma das últimas `minimo` semanas andou tanto."""
    anteriores = [h for h in historico if h["ref1"] < ref1]
    if abs(var) < 5 or len(anteriores) < minimo:
        return ""
    tipo = "abertura" if var > 0 else "fechamento"
    iguais = [i for i, h in enumerate(anteriores) if (h[chave] >= var if var > 0 else h[chave] <= var)]
    if not iguais:
        return f"maior {tipo} semanal em {len(anteriores)} semanas"
    if len(anteriores) - 1 - iguais[-1] < minimo:
        return ""
    return f"maior {tipo} semanal desde {anteriores[iguais[-1]]['ref1']:%d/%m/%Y}"


def spread(sp):
    d = sp["variacao_bps"]
    if d:
        txt = f"{'Abertura' if d > 0 else 'Fechamento'} de aproximadamente {abs(d)} bps, chegando a cerca de"
    else:
        txt = "Estável, em cerca de"
    txt += f" {sp['nivel_bps']} bps"
    if sp.get("contexto"):
        txt += f" ({sp['contexto']})"
    txt += f", {sp.get('referencia') or 'no comparativo semanal reportado pela ' + sp['fonte']}."
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
    """Taxa na convenção do papel: "DI + 1,95%", "IPCA + 7,83%", "105,20% do DI"."""
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


def ipca(p):
    """Papel IPCA+ com spread sobre a NTN-B: nível e variação saem em bps de spread, não de taxa."""
    return "ntnb" in p


def nivel(p, x):
    if ipca(p):
        s = round(x)
        return f"NTN-B {p['ntnb']} {'-' if s < 0 else '+'} {abs(s)} bps"
    return fmt_taxa(p["indice"], x)


def variacao(p, d):
    if ipca(p):
        return "estável" if round(d) == 0 else f"{round(d):+d} bps"
    return delta(p["indice"], d)


def papel_mercado(t, p):
    """Negócio na B3 (sem passagens) primeiro; a taxa indicativa da ANBIMA é marcação, não preço."""
    if not p:
        return f"{t}: sem taxa indicativa ANBIMA nem negócios na B3"
    partes = []
    if p.get("neg1") is not None:
        txt = f"{volume_txt(p.get('volume_mm'))} a {nivel(p, p['neg1'])}"
        if p.get("neg0") is not None:
            txt += f" ({variacao(p, p['neg1'] - p['neg0'])} ante a média da semana anterior)"
        partes.append(txt)
    elif vt := volume_txt(p.get("volume_mm")):  # sem taxa média: nenhum negócio a partir de R$ 1 mi
        partes.append(f"{vt}, todos abaixo de R$ 1 mi por negócio" if (p.get("volume_mm") or 0) >= 1 else vt)
    chave1, chave0 = ("spread1", "spread0") if ipca(p) else ("taxa1", "taxa0")
    if p.get(chave1) is None:
        partes.append("sem taxa indicativa ANBIMA")
    else:
        ind = f"indicativa ANBIMA {nivel(p, p[chave1])}"
        if p.get("par") is not None:
            ind += f", {p['par']:.2f}% do par".replace(".", ",")
        if p.get(chave0) is not None:
            ind += f", {variacao(p, p[chave1] - p[chave0])} na semana"
        partes.append(ind)
    return f"{t}: " + "; ".join(partes)


def linha_mercado(tickers, papeis):
    """Bloco "Mercado:" de uma notícia: um papel por linha."""
    return "Mercado:\n" + "\n".join(f"• {papel_mercado(t, papeis.get(t, {}))}." for t in tickers)


def topico(n, papeis):
    linhas = [n["titulo"], n["texto"].strip()]
    if n.get("tickers") and papeis:
        linhas.append(linha_mercado(n["tickers"], papeis))
    return "\n".join(linhas)


def secao(titulo, blocos):
    """Título em caixa alta junto do primeiro bloco; seção sem conteúdo some."""
    blocos = [x for x in blocos if x]
    return [f"{titulo}\n{blocos[0]}", *blocos[1:]] if blocos else []


def ntnb_proxima(curva, venc):
    """(ano, taxa) da NTN-B com vencimento mais próximo de `venc` (MM/AAAA): referência de emissão nova."""
    m, a = venc.split("/")
    alvo = date(int(a), int(m), 15)
    chave = min(curva, key=lambda k: abs((datetime.strptime(k, "%d/%m/%Y").date() - alvo).days))
    return chave[-4:], curva[chave]


def taxa_emissao(i, curva):
    """Taxa da emissão; IPCA+ ganha o spread sobre a NTN-B, a mesma convenção das linhas de mercado."""
    m = re.match(r"IPCA \+ ([\d,]+)%", i["taxa"])
    if not (m and curva and re.fullmatch(r"\d{2}/\d{4}", i["vencimento"])):
        return i["taxa"]
    ano, b = ntnb_proxima(curva, i["vencimento"])
    s = round(spread_over_bps(num_br(m.group(1)), b))
    return f"{i['taxa']} (NTN-B {ano} {'-' if s < 0 else '+'} {abs(s)} bps)"


def item_emissao(i, papeis, curva=None):
    extras = [f"rating {i['rating']}"] if i.get("rating") else []
    sec = [f"{t} a {nivel(papeis[t], papeis[t]['spread1' if ipca(papeis[t]) else 'taxa1'])}"
           for t in i.get("tickers", []) if papeis.get(t, {}).get("taxa1") is not None]
    if sec:
        extras.append("secundário do emissor (indicativa ANBIMA): " + ", ".join(sec))
    return (f"• {i['emissor']}: {taxa_emissao(i, curva)}, {mi(i['volume_mm'])}, vencimento {i['vencimento']}"
            + "".join(f"; {x}" for x in extras) + ".")


def emissoes(e, papeis, curva=None):
    nomes = e.get("aguardando_bookbuilding")
    frase = f"{lista(nomes)} {'tem' if len(nomes) == 1 else 'têm'} bookbuilding previsto para os próximos dias." if nomes else ""
    itens = [item_emissao(i, papeis, curva) for i in e.get("itens", [])]
    return [itens and "Taxas de emissão na semana (registro na CVM):\n" + "\n".join(itens),
            f"{frase} {e.get('comentario', '')}".strip()]


def volume(nome, atual, anterior):
    ante = f", ante R$ {bi(anterior)} bi na semana anterior" if anterior is not None else ""
    return f"{nome}: R$ {bi(atual)} bi{ante}."


def render(s, mercado=None, historico=()):
    """Texto do relatório. `mercado`: dados da semana gravados pelo coletar.py; `historico`: spreads semanais."""
    ini, fim = s["semana_inicio"], s["semana_fim"]
    m = mercado or {}
    papeis = m.get("papeis", {})
    ix, v = s.get("indices", {}), s.get("volumes") or m.get("volumes")
    if not {"ida_di", "cdi", "ida_ipca", "ima_b"} <= ix.keys():
        ix = {**indices_semana(ini, fim), **ix}
    infra = dict(s.get("spread_infra") or spread_infra(ini, fim))
    trad = dict(s.get("spread_tradicionais") or spread_tradicionais(ini, fim))
    if historico and "janela" in ix:
        infra["contexto"] = contexto(historico, "infra_var", infra["variacao_bps"], ix["janela"][1])
        trad["contexto"] = contexto(historico, "trad_var", trad["variacao_bps"], ix["janela"][1])
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
        *secao("EMISSÕES CORPORATIVAS", emissoes(s["emissoes"], papeis, m.get("ntnb")) if "emissoes" in s else []),
        *secao("CRÉDITO BANCÁRIO", ["\n".join(f"• {r}" for r in c.get("ratings", [])), c.get("comentario")]),
        *secao("RADAR", [topico(n, papeis) for n in s.get("radar", [])]),
        DISCLAIMER_FINAL,
        s.get("fechamento", FECHAMENTO),
        f"Abraço,\n{s.get('assinatura', ASSINATURA)}",
        METODOLOGIA,
        f"Fontes: {lista(s['fontes'])}.",
    ]
    return "\n\n".join(x for x in b if x) + "\n"


# ----------------------------------------------------------------- verificação
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿]")
SINGULAR = re.compile(r"\b(eu|me|meu|minha|meus|minhas|comigo)\b", re.I)
ENCHIMENTO = ("seguimos acompanhando", "acompanhamos a demanda", "métricas distintas", "métricas diferentes")
SERIE_EMISSAO = re.compile(r"\d+ª séries? d[ae] \d+ª emiss", re.I)


def sem_acento(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def validar(s, txt, bruto, mercado=None):
    """Problemas que impedem o envio. Lista vazia = pode gerar o texto."""
    problemas = []
    if m := EMOJI.search(txt):
        problemas.append(f"emoji no texto: {m.group()!r}")
    if m := SINGULAR.search(txt):
        problemas.append(f"primeira pessoa do singular: {m.group()!r}")
    problemas += [f"frase de enchimento: {f!r}" for f in ENCHIMENTO if f in txt.lower()]
    if m := SERIE_EMISSAO.search(txt):
        problemas.append(f"série/emissão por extenso em vez de ticker: {m.group()!r}")
    for n in s.get("noticias", []):
        if (k := len(n["texto"].split())) > LIMITE_PALAVRAS:
            problemas.append(f"notícia {n['titulo']}: {k} palavras, limite {LIMITE_PALAVRAS}")
    # Toda fonte listada precisa aparecer em algum "# fonte:" do arquivo da semana.
    comentarios = re.sub(r"[^a-z0-9&]", "", sem_acento(" ".join(re.findall(r"#\s*fonte:(.*)", bruto, re.I))))
    for f in s.get("fontes", []):
        if f in FONTES_AUTOMATICAS:
            continue
        chave = re.sub(r"[^a-z0-9&]", "", sem_acento(f))
        if not any(a in comentarios for a in (chave, *FONTE_ALIAS.get(chave, ()))):
            problemas.append(f"fonte {f!r} listada sem nenhum '# fonte:' correspondente")
    # Emissão com indexador inferido pelo painel só entra com a conferência registrada.
    origem = (mercado or {}).get("origem_indexador", {})
    for i in s.get("emissoes", {}).get("itens", []):
        req = str(i.get("requerimento", ""))
        if origem and not req:
            problemas.append(f"emissão {i['emissor']}: falta o campo requerimento")
        elif origem.get(req, "declarado na taxa") != "declarado na taxa" and not i.get("conferido"):
            problemas.append(f"emissão {i['emissor']}: indexador {origem[req]!r} sem o campo conferido")
    return problemas


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if sys.argv[1:] == ["--historico"]:
        reconstruir_historico()
        return
    arq = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("exemplo.toml")
    bruto = arq.read_text(encoding="utf-8")
    s = tomllib.loads(bruto)
    m = arq.parent / f"{s['semana_fim']}_mercado.json"
    mercado = json.loads(m.read_text(encoding="utf-8")) if m.exists() else None
    ini, fim = s["semana_inicio"], s["semana_fim"]
    automatico = "spread_infra" not in s and "spread_tradicionais" not in s
    s.setdefault("spread_infra", spread_infra(ini, fim))
    s.setdefault("spread_tradicionais", spread_tradicionais(ini, fim))
    historico = ler_historico()
    txt = render(s, mercado, historico)
    if problemas := validar(s, txt, bruto, mercado):
        for p in problemas:
            print(f"[erro] {p}", file=sys.stderr)
        raise SystemExit("Relatório não gerado: corrija o arquivo da semana e rode de novo.")
    if automatico:
        _, ref0, ref1 = referencias(ini, fim)
        gravar_historico(historico + [linha_historico(ref0, ref1, s["spread_infra"], s["spread_tradicionais"])])
    saida = Path(__file__).with_name("saida") / f"relatorio_{s['semana_fim']}.txt"
    saida.parent.mkdir(exist_ok=True)
    saida.write_text(txt, encoding="utf-8")
    print(txt)
    print(f"[salvo em {saida}]", file=sys.stderr)


if __name__ == "__main__":
    main()
