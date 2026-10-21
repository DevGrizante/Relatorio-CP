"""Dossiê da semana: dados brutos que a tarefa agendada usa para escrever o relatório.

Uso:
    python coletar.py              (semana passada, segunda a sexta)
    python coletar.py 2026-10-16   (semana que termina nessa sexta)

Grava semanas/<sexta>_dossie.md com:
  - emissões com book na semana e próximos books (painel cvm-monitor-pro);
  - documentos da CVM (IPE) que mexem com crédito, só de emissores do universo;
  - emissores mais negociados no secundário (base do highlights_app);
  - o universo: emissores da carteira teórica do IDA-Geral (ANBIMA) e bancos.
Uma fonte que falhar vira aviso no dossiê; as outras seções saem mesmo assim.
"""
import csv
import gzip
import html
import io
import json
import re
import sys
import unicodedata
import urllib.request
import zipfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from relatorio import baixar, delta, fmt_taxa, num_br, periodo, referencias, spreads_b, taxas_anbima

AQUI = Path(__file__).parent
PAINEL = AQUI.parent / "cvm-monitor-pro" / "backend"
HIGHLIGHTS = AQUI.parent / "highlights_app"
ANBIMA_LOCAL = Path(r"E:\programacao\Emissoes\Data_set\Anbima")  # exports manuais da ANBIMA Data
EMISSAO_MINIMA_MM = 100  # emissão menor que isso não entra no relatório
SND = ("https://www.debentures.com.br/exploreosnd/consultaadados/emissoesdedebentures/"
       "caracteristicas_e.asp?tip_deb=&op_exc=False")

# Documentos do IPE que interessam ao crédito; o resto (AGE, RCA, posições...) é ruído.
CATEGORIAS = {
    "Fato Relevante",
    "Aviso aos Debenturistas",
    "Escrituras e aditamentos de debêntures",
    "Informações de Companhias em Recuperação Judicial ou Extrajudicial",
}
CATEGORIA_TIPO = {
    ("Assembleia", "AGDEB"),
    ("Dados Econômico-Financeiros", "Relatório de Agência de Rating"),
    ("Comunicado ao Mercado", "Outros Comunicados Não Considerados Fatos Relevantes"),
}

# Coluna `indexador_inferido` do painel. Inferido = a taxa não nomeia o índice e o
# painel deduziu CDI/IPCA/PRÉ por banda numérica ou tipo do ativo (ex.: SABESP 28127,
# "3,50% ao ano" vira "CDI / DI" só por ser ≤ 6%). Painel sem a coluna (servidor
# antigo) não pode passar por declarado.
ORIGEM_INDEXADOR = {False: "declarado na taxa", True: "inferido — conferir na escritura"}


def origem_indexador(r):
    if r["indexador"] == "Não Informado":  # sem índice nenhum: nada a declarar nem a inferir
        return "—"
    return ORIGEM_INDEXADOR.get(r.get("indexador_inferido"), "não informado pelo painel")


def ultima_sexta(hoje):
    return hoje - timedelta(days=(hoje.weekday() - 4) % 7 or 7)


def norm(nome):
    """Razão social comparável entre ANBIMA e CVM: sem acento, pontuação e forma societária."""
    s = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode().upper()
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = re.sub(r"\b(S A|SA|S|A|LTDA|CIA|COMPANHIA|EM RECUPERACAO (JUDICIAL|EXTRAJUDICIAL))\b", " ", s)
    return " ".join(s.split())


def banco(n):
    return "BANCO" in n.split() or n.startswith("BCO ")


def iso(br):
    return f"{br[6:]}-{br[3:5]}-{br[:2]}" if len(br) == 10 else ""


def tabela(cab, linhas):
    if not linhas:
        return "_nenhum item_"
    cel = lambda v: " ".join(str(v).split()).replace("|", "/")
    return "\n".join(["| " + " | ".join(cab) + " |", "|" + "---|" * len(cab)]
                     + ["| " + " | ".join(map(cel, l)) + " |" for l in linhas])


def chaves(cnpj, nome):
    """Chaves de busca de um emissor: CNPJ só com dígitos e razão social normalizada (nunca colidem)."""
    return {digitos(cnpj), norm(nome)} - {""}


def por_emissor(d, cnpj, nome):
    return next((d[k] for k in (digitos(cnpj), norm(nome)) if k in d), None)


def universo(fim):
    """Carteira teórica do IDA-Geral vigente em `fim`.

    Devolve (emissores [(nome, peso %)] do maior para o menor, nomes normalizados, CNPJs,
    peso do emissor por chave de `chaves`, ticker -> CNPJ do painel)."""
    txt = baixar("https://www.anbima.com.br/informacoes/ida/IDA_down.asp", {
        "tipo": "ct", "indice": "GERAL", "escolha": "2", "Idioma": "PT", "saida": "csv",
        "DataIniD": f"{fim:%d/%m/%Y}", "DataIniM": f"{fim:%m/%Y}"}).decode("latin-1")
    linhas = [c for c in (l.split(";") for l in txt.splitlines()[2:]) if len(c) > 8]
    if not linhas:
        raise RuntimeError("ANBIMA sem carteira teórica do IDA")
    # A ANBIMA não traz CNPJ; o painel já casa ticker com CNPJ do emissor.
    sm = json.loads((PAINEL / "secondary_market.json").read_text(encoding="utf-8"))
    tk_cnpj = {v["ticker"]: k.split("_")[0] for k, v in sm.items() if v.get("ticker")}
    peso_nome = {c[1]: num_br(c[8]) for c in linhas}  # "Peso do Emissor (%)" repete em cada papel
    peso = {}
    for c in linhas:
        for k in chaves(tk_cnpj.get(c[0], ""), c[1]):
            peso[k] = num_br(c[8])
    emissores = sorted(peso_nome.items(), key=lambda x: -x[1])
    return (emissores, {norm(e) for e in peso_nome}, {tk_cnpj[c[0]] for c in linhas if c[0] in tk_cnpj},
            peso, tk_cnpj)


def painel():
    """Dataset do painel: do servidor local se estiver aberto; senão, carrega o engine aqui."""
    try:
        bruto = urllib.request.urlopen("http://localhost:8080/api/dataset", timeout=120).read()
    except OSError:
        sys.path.insert(0, str(PAINEL))
        from data_engine import engine
        # Só leitura: sem os workers que baixam PDFs e regravam os caches do painel.
        engine._start_booking_worker = engine._start_sre_background_worker = lambda: None
        engine._start_scheduler_worker = lambda: None
        engine.ensure_data()
        bruto = engine.columnar_gzip
    d = json.loads(gzip.decompress(bruto))
    return [dict(zip(d["cols"], r)) for r in d["rows"]]


def books(linhas, de, ate):
    """Debêntures e notas comerciais de pelo menos R$ 100 mi com book entre `de` e `ate`, maiores primeiro."""
    sel = [r for r in linhas if re.search("DEB|NOTA", r["setor_ativo"].upper())
           and (r["volume"] or 0) >= EMISSAO_MINIMA_MM * 1e6
           and de.isoformat() <= iso(r["dt_booking"]) <= ate.isoformat()]
    return sorted(sel, key=lambda r: -(r["volume"] or 0))


def secundario(debs_cnpj, cnpj, papeis):
    """Papéis do mesmo emissor com taxa indicativa ANBIMA: a régua para dizer se a emissão saiu cara ou barata."""
    regs = sorted(debs_cnpj.get(digitos(cnpj), []), key=lambda r: iso(r["vencimento"]))
    cot = [f"{r['ticker']} {fmt_taxa(papeis[r['ticker']]['indice'], papeis[r['ticker']]['taxa1'])} "
           f"(venc. {r['vencimento'][3:]})" for r in regs if papeis.get(r["ticker"], {}).get("taxa1") is not None]
    return "; ".join(cot) or "—"


def emissoes(sel, debs_cnpj, papeis):
    def taxa(r):
        series = r.get("Series") or []
        if len(series) < 2:
            return r["taxa"]
        return "; ".join(f"série {k}: {s.get('taxa') or 'n/d'} (venc. {s.get('venc') or 'n/d'}, "
                         f"R$ {(s.get('vol') or 0) / 1e6:.0f} mi)" for k, s in enumerate(series, 1))

    return tabela(
        ["Book", "Fonte da data", "Emissor", "Instrumento", "R$ mi", "Indexador", "Origem do indexador",
         "Taxa (texto CVM)", "Vencimento", "Status", "Requerimento", "Secundário do emissor (ANBIMA)"],
        [[r["dt_booking"], r["dt_booking_fonte"], r["emissor"], r["setor_ativo"], f"{(r['volume'] or 0) / 1e6:.0f}",
          r["indexador"], origem_indexador(r), taxa(r), r["vencimento"], r["status"], r["id"],
          secundario(debs_cnpj, r["cnpj_emissor"], papeis)] for r in sel])


def docs_credito(de, ate, nomes, cnpjs):
    """Documentos do IPE entregues entre `de` e `ate` que mexem com crédito, só de emissores do universo."""
    docs = []
    for ano in sorted({de.year, ate.year}):
        z = zipfile.ZipFile(io.BytesIO(baixar(
            f"https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_{ano}.zip")))
        docs += csv.DictReader(io.StringIO(z.read(z.namelist()[0]).decode("latin-1")), delimiter=";")
    sel = [x for x in docs
           if de.isoformat() <= x["Data_Entrega"] <= ate.isoformat()
           and (x["Categoria"] in CATEGORIAS or (x["Categoria"], x["Tipo"]) in CATEGORIA_TIPO)
           and not rotina(x["Assunto"])
           and (x["CNPJ_Companhia"] in cnpjs or norm(x["Nome_Companhia"]) in nomes or banco(norm(x["Nome_Companhia"])))]
    return sorted(sel, key=lambda x: (x["Data_Entrega"], x["Nome_Companhia"]))


def pct_br(p):
    return "—" if p is None else f"{p:.2f}".replace(".", ",")


def mi_br(v):
    return "—" if not v else f"{v / 1e6:.1f}".replace(".", ",")


def documentos(sel, peso, vol, dmax):
    """Tabela dos documentos com as métricas de desempate da régua de notícias."""
    return tabela(["Entrega", "Emissor", "Maior Δ taxa semana (bps)", "Peso IDA (%)", "Secundário semana R$ mi",
                   "Categoria", "Tipo", "Assunto", "Link"],
                  [[x["Data_Entrega"], x["Nome_Companhia"],
                    por_emissor(dmax, x["CNPJ_Companhia"], x["Nome_Companhia"]) or "—",
                    pct_br(por_emissor(peso, x["CNPJ_Companhia"], x["Nome_Companhia"])),
                    mi_br(por_emissor(vol, x["CNPJ_Companhia"], x["Nome_Companhia"])),
                    x["Categoria"], x["Tipo"], html.unescape(x["Assunto"]), x["Link_Download"]] for x in sel])


def digitos(s):
    return re.sub(r"\D", "", s or "")


def numero(s):
    s = str(s).strip()
    return str(int(s)) if s.isdigit() else s


def snd():
    """Debêntures registradas no SND (ANBIMA/B3): público, sem login nem reCAPTCHA."""
    linhas = baixar(SND).decode("latin-1").splitlines()
    i = next(k for k, l in enumerate(linhas) if l.startswith("Codigo do Ativo"))
    cab = [c.strip() for c in linhas[i].split("\t")]
    regs = (dict(zip(cab, (c.strip() for c in l.split("\t")))) for l in linhas[i + 1:])
    return [{"emissor": r["Empresa"], "cnpj": r.get("CNPJ", ""), "ticker": r["Codigo do Ativo"],
             "emissao": numero(r["Emissao"]), "serie": numero(r["Serie"]),
             "vencimento": r["Data de Vencimento"], "indice": r.get("indice", ""),
             "incentivada": r.get("Deb. Incent. (Lei 12.431)") == "S"}
            for r in regs if r.get("Situacao") == "Registrado"]


def anbima_local(arq):
    """Export "Títulos Privados - Características" baixado da ANBIMA Data (exige login, então é manual)."""
    import pandas as pd  # só no plano B: o caminho normal não depende de pandas
    regs = pd.read_excel(arq, engine="openpyxl", dtype=str).fillna("").to_dict("records")
    return [{"emissor": r["Emissor"], "cnpj": "", "ticker": r["Código"], "emissao": numero(r["Emissão"]),
             "serie": numero(r["Série"]), "vencimento": r["Data de vencimento"], "indice": r["Remuneração"],
             "incentivada": r["Lei 12.431"] == "Sim"}
            for r in regs if r["Tipo"] == "Debênture"]


def debentures():
    """(debêntures com ticker, emissão e série; fonte usada). SND primeiro; sem ele, último export local da ANBIMA."""
    try:
        return snd(), "SND (debentures.com.br)"
    except Exception as e:
        arq = max(ANBIMA_LOCAL.glob("titulos-privados-caracteristicas-*.xls"), key=lambda p: p.stat().st_mtime)
        return anbima_local(arq), f"ANBIMA Data, arquivo local {arq.name} (SND indisponível: {e!r})"


def tickers(debs, cnpjs, nomes, papeis):
    """De-para emissão/série -> ticker dos emissores citados, com taxa ANBIMA, variação e volume da semana."""
    cnpjs = cnpjs - {""}  # CNPJ vazio (export local da ANBIMA não traz) não pode casar com tudo
    sel = sorted((r for r in debs if digitos(r["cnpj"]) in cnpjs or norm(r["emissor"]) in nomes),
                 key=lambda r: (r["emissor"], r["emissao"].zfill(4), r["serie"].zfill(4)))

    def mercado(t):
        p = papeis.get(t, {})
        taxa = fmt_taxa(p["indice"], p["taxa1"]) if p.get("taxa1") is not None else "—"
        var = delta(p["indice"], p["taxa1"] - p["taxa0"]) if None not in (p.get("taxa0"), p.get("taxa1")) else "—"
        vol = "—" if p.get("volume_mm") is None else f"{p['volume_mm']:.1f}".replace(".", ",")
        return [taxa, var, vol]

    return tabela(["Emissor", "Ticker", "Emissão", "Série", "Vencimento", "Índice", "Taxa ANBIMA", "Δ semana",
                   "Volume B3 semana R$ mi"],
                  [[r["emissor"], r["ticker"], r["emissao"], r["serie"], r["vencimento"], r["indice"], *mercado(r["ticker"])]
                   for r in sel])


def gravar_tickers(debs, caminho):
    with open(caminho, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["emissor", "ticker", "emissao", "serie", "vencimento", "indice"])
        w.writerows([r["emissor"], r["ticker"], r["emissao"], r["serie"], r["vencimento"], r["indice"]] for r in debs)


def rotina(assunto):
    """Aviso de pagamento de juros/amortização no cronograma: não é evento de crédito."""
    a = assunto.lower()
    return "pagamento de juros" in a and "resgate" not in a


def trades_semana(ini, fim):
    """([(emissor, ticker, volume R$)] dos negócios de debêntures na B3, dias sem base no Highlights)."""
    sys.path.insert(0, str(HIGHLIGHTS))
    from highlights_py import config, fontes
    trades, sem_base = [], []
    for d in (ini + timedelta(i) for i in range((fim - ini).days + 1)):
        # Só lê o cache diário do Highlights. Indexar a base daqui gravaria cache
        # vazio para um pregão que ainda não entrou no data_base.xlsx.
        if not (config.DIR_CACHE / f"trades_{d.isoformat()}.pkl").exists():
            sem_base.append(d)
            continue
        df = fontes.negocios(d)
        trades += [(e, t, v or 0) for e, t, v in zip(df["emissor"], df["ticker"], df["volume"])]
    return trades, sem_base


def completa(sem_base, taxas_cdi, de, ate):
    """Semana sem dia útil faltando na base do Highlights. Feriado sem base não conta."""
    return not set(sem_base) & {d for d, _ in taxas_cdi if de <= d <= ate}


def papeis_semana(ref0, ref1, trades, debs):
    """{ticker: remuneração, taxa ANBIMA nos dois fechamentos, volume B3 em R$ mi (None se a base estiver incompleta)}.

    IPCA+ leva ainda o spread em bps sobre a NTN-B de referência nos dois fechamentos e o ano dessa NTN-B."""
    a, b = taxas_anbima(ref0), taxas_anbima(ref1)
    sa, sb = spreads_b(ref0), spreads_b(ref1)
    vol = Counter()
    for _, t, v in trades or []:
        vol[t] += v
    todos = set(a) | set(b) | set(vol) | {r["ticker"] for r in debs}
    return {t: {"indice": (b.get(t) or a.get(t) or ("",))[0],
                "taxa0": a[t][1] if t in a else None, "taxa1": b[t][1] if t in b else None,
                "volume_mm": None if trades is None else vol[t] / 1e6,
                **({"spread0": sa[t][0] if t in sa else None, "spread1": sb[t][0], "ntnb": sb[t][1]}
                   if t in sb else {})} for t in todos}


def volumes(trades, trades_ant, debs):
    """Volume na B3 em R$ bi, separado pela flag de incentivada (Lei 12.431) do cadastro de debêntures."""
    incent = {r["ticker"] for r in debs if r["incentivada"]}

    def soma(tr, flag):
        return round(sum(v for _, t, v in tr if (t in incent) == flag) / 1e9, 1)

    out = {"incentivadas": soma(trades, True), "nao_incentivadas": soma(trades, False),
           "comentario": "Volume registrado na B3, inclusive operações de passagem."}
    if trades_ant is not None:
        out |= {"incentivadas_ant": soma(trades_ant, True), "nao_incentivadas_ant": soma(trades_ant, False)}
    return out


def maior_delta(debs, papeis):
    """Maior variação absoluta de taxa (bps) entre os papéis de cada emissor, por chave de `chaves`."""
    out = {}
    for r in debs:
        p = papeis.get(r["ticker"], {})
        if None in (p.get("taxa0"), p.get("taxa1")) or "do DI" in p["indice"]:
            continue
        d = abs(round((p["taxa1"] - p["taxa0"]) * 100))
        for k in chaves(r["cnpj"], r["emissor"]):
            out[k] = max(out.get(k, 0), d)
    return out


def volume_por_emissor(trades, tk_cnpj):
    """Volume da semana por chave de `chaves`: CNPJ via ticker e, de reserva, o nome do Highlights."""
    vol = Counter()
    for e, t, v in trades:
        for k in chaves(tk_cnpj.get(t, ""), e):
            vol[k] += v
    return vol


def negociados(trades, sem_base):
    vol, qtd = Counter(), Counter()
    for e, _, v in trades:
        vol[e] += v
        qtd[e] += 1
    dias = ", ".join(f"{d:%d/%m}" for d in sem_base)
    aviso = f"Dias sem base no Highlights (feriado ou base não atualizada): {dias}.\n\n" if sem_base else ""
    return aviso + tabela(["Emissor", "Volume bruto R$ mi (inclui passagens)", "Negócios"],
                          [[e, f"{v / 1e6:.1f}", qtd[e]] for e, v in vol.most_common(15)])


def tentar(f):
    try:
        return f(), None
    except Exception as e:  # dossiê sai mesmo com uma fonte fora do ar
        return None, e


def bloco(titulo, gerar):
    corpo, erro = tentar(gerar)
    return f"## {titulo}\n\n{corpo if erro is None else f'**Indisponível:** {erro!r}'}"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    fim = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else ultima_sexta(date.today())
    ini = fim - timedelta(days=4)
    domingo = fim + timedelta(days=2)  # fato relevante de fim de semana entra nesta semana

    def exigir(valor, erro):
        if valor is None:
            raise RuntimeError(erro)
        return valor

    u, erro_u = tentar(lambda: universo(fim))
    linhas, erro_p = tentar(painel)
    semana, erro_s = tentar(lambda: books(exigir(linhas, erro_p), ini, fim))
    proximos, erro_x = tentar(lambda: books(exigir(linhas, erro_p), fim + timedelta(days=1), fim + timedelta(days=7)))
    docs, erro_d = tentar(lambda: docs_credito(ini, domingo, *exigir(u, erro_u)[1:3]))
    debs, erro_t = tentar(debentures)
    regs = debs[0] if debs else []
    refs, erro_r = tentar(lambda: referencias(ini, fim))
    tr, erro_h = tentar(lambda: trades_semana(ini, fim))
    tr_ant, _ = tentar(lambda: trades_semana(ini - timedelta(days=7), fim - timedelta(days=7)))
    # Volume só vale com a semana inteira na base do Highlights; semana parcial sairia subestimada.
    ok = refs and tr and completa(tr[1], refs[0], ini, fim)
    ok_ant = refs and tr_ant and completa(tr_ant[1], refs[0], ini - timedelta(days=7), fim - timedelta(days=7))
    papeis, erro_m = tentar(lambda: papeis_semana(refs[1], refs[2], tr[0] if ok else None, regs))
    papeis = papeis or {}
    # Emissores citados no dossiê: é para eles que a tarefa precisa trocar "Xª série da Yª emissão" pelo ticker.
    cnpjs = {digitos(x["CNPJ_Companhia"]) for x in docs or []} | {digitos(r["cnpj_emissor"]) for r in semana or []}
    nomes = {norm(x["Nome_Companhia"]) for x in docs or []} | {norm(r["emissor"]) for r in semana or []}
    arq_tickers = AQUI / "semanas" / "tickers.csv"
    # Régua de notícias: maior variação de taxa do emissor, peso no IDA e volume no secundário.
    tk_cnpj = (u[4] if u else {}) | {r["ticker"]: r["cnpj"] for r in regs if r["cnpj"]}
    peso = u[3] if u else {}
    vol = volume_por_emissor(tr[0], tk_cnpj) if ok else {}
    dmax = maior_delta(regs, papeis)
    debs_cnpj = {}
    for r in regs:
        debs_cnpj.setdefault(digitos(r["cnpj"]), []).append(r)

    partes = [
        f"# Dossiê da semana {periodo(ini, fim)}/{fim.year}",
        f"Gerado por coletar.py em {date.today():%d/%m/%Y}. Dados brutos: conferir antes de usar no relatório.",
        f"Janela de mercado: fechamento ANBIMA de {refs[1]:%d/%m} a {refs[2]:%d/%m}." if refs
        else f"**Janela de mercado indisponível:** {erro_r!r}",
        "" if ok else "**Base B3 (Highlights) incompleta na semana:** volumes ficam fora do relatório.",
        bloco(f"Emissões com book na semana, a partir de R$ {EMISSAO_MINIMA_MM} mi (painel CVM)",
              lambda: emissoes(exigir(semana, erro_s), debs_cnpj, papeis)),
        bloco("Próximos books, 7 dias (datas BKB e AVM são previsões)",
              lambda: emissoes(exigir(proximos, erro_x), debs_cnpj, papeis)),
        bloco(f"Documentos CVM de {ini:%d/%m} a {domingo:%d/%m}: emissores do universo",
              lambda: documentos(exigir(docs, erro_d), peso, vol, dmax)),
        bloco(f"Tickers dos emissores citados, com mercado da semana (fonte: {debs[1] if debs else 'indisponível'}; "
              f"demais emissores em {arq_tickers.name})",
              lambda: tickers(exigir(debs, erro_t)[0], cnpjs, nomes, papeis)),
        bloco("Mais negociados no secundário na semana (B3, base do Highlights)",
              lambda: negociados(*exigir(tr, erro_h))),
        bloco("Universo de emissores",
              lambda: f"Bancos: qualquer instituição financeira emissora de CDB, LF, LCA ou LCI.\n\n"
                      f"IDA-Geral, carteira vigente em {fim:%d/%m} ({len(exigir(u, erro_u)[0])} emissores, "
                      f"com o peso de cada um no índice, do maior para o menor): "
                      + "; ".join(f"{n} ({pct_br(p)}%)" for n, p in u[0]) + "."),
    ]
    saida = AQUI / "semanas" / f"{fim}_dossie.md"
    saida.parent.mkdir(exist_ok=True)
    if debs:
        gravar_tickers(debs[0], arq_tickers)
    saida.write_text("\n\n".join(p for p in partes if p) + "\n", encoding="utf-8")
    # Dados que o relatorio.py usa direto, sem passar pela redação da tarefa.
    mercado = {"janela": [str(d) for d in refs[1:]] if refs else None, "papeis": papeis}
    if ok and regs:
        mercado["volumes"] = volumes(tr[0], tr_ant[0] if ok_ant else None, regs)
    arq_mercado = AQUI / "semanas" / f"{fim}_mercado.json"
    arq_mercado.write_text(json.dumps(mercado, ensure_ascii=False), encoding="utf-8")
    print(f"semana_inicio = {ini}\nsemana_fim = {fim}\ndossie = {saida}\nmercado = {arq_mercado}")


if __name__ == "__main__":
    main()
