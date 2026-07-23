"""
Cobli - Onboarding Tech Touch | Jornada de 90 dias
===================================================
A partir do ID da empresa (HubSpot), posiciona o cliente na jornada de 90 dias
(meta: Basic Value >= 3), mostra o que ele já fez, sinaliza fases fora do prazo
e monta as comunicações de WhatsApp de cada fase, com vídeos tutoriais e link
de envio direto (wa.me).

Como rodar:
    pip install -r requirements.txt
    streamlit run app.py
"""

import os
import re
from urllib.parse import quote

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Cobli · Jornada de Onboarding", page_icon="🚚", layout="wide")

ANALISTA_PADRAO = "Igor"
INVITE_BASE_URL = "https://cadastro.cobli.co/invites/{}"
META_BASIC_VALUE = 3.0
JORNADA_DIAS = 90
def _detectar_videos_dir():
    for d in ("videos", "videos2", "video", "Videos"):
        if os.path.isdir(d):
            return d
    return "videos"


VIDEOS_DIR = _detectar_videos_dir()

# URL pública base dos vídeos (opcional). Se hospedar a pasta videos/ no GitHub,
# use algo como: https://raw.githubusercontent.com/USUARIO/REPO/main/videos
# Pode também ser sobrescrito no campo da barra lateral.
VIDEOS_BASE_URL = ""

# Fases da jornada (por dia desde a assinatura do contrato)
FASES = [
    {"nome": "Boas-vindas & Instalação", "ini": 0, "fim": 15, "emoji": "📦"},
    {"nome": "Configuração inicial (Setup)", "ini": 16, "fim": 45, "emoji": "⚙️"},
    {"nome": "Segurança & gestão", "ini": 46, "fim": 75, "emoji": "🛡️"},
    {"nome": "Consolidação & valor", "ini": 76, "fim": 90, "emoji": "🎯"},
]

# Critérios do Basic Value que devem estar prontos ao fim de cada fase
PHASE_CRITERIA = {
    0: ["instalation_completeness_grade_bom"],
    1: ["setup_user_bom", "setup_grups_bom", "setup_drivers_bom", "setup_driver_identification_bom"],
    2: ["basic_config_speed_limit_bom", "basic_config_fleet_policy_bom",
        "basic_config_geofences_bom", "basic_config_checklists_bom"],
    3: [],  # consolidação: meta é basic_value_score >= 3
}

# Texto de ação por critério (o que o cliente precisa fazer)
COL_TEXT = {
    "instalation_completeness_grade_bom": "Concluir a instalação dos equipamentos (mais de 90% da frota ativa)",
    "setup_user_bom": "Criar pelo menos 2 perfis de usuário para a sua equipe",
    "setup_grups_bom": "Organizar os veículos em pelo menos 2 grupos",
    "setup_drivers_bom": "Cadastrar pelo menos 2 motoristas",
    "setup_driver_identification_bom": "Identificar o motorista em pelo menos 60% das viagens",
    "basic_config_speed_limit_bom": "Configurar o limite de velocidade em pelo menos 60% dos veículos",
    "basic_config_fleet_policy_bom": "Criar pelo menos 2 regras de política de frota",
    "basic_config_geofences_bom": "Criar pelo menos 2 locais de interesse (geofences)",
    "basic_config_checklists_bom": "Criar pelo menos 2 checklists",
}

# Vídeo tutorial por critério: coluna _bom -> (arquivo em videos/, título)
VIDEO_MAP = {
    "setup_user_bom": ("usuarios.mp4", "Cadastrar usuários"),
    "setup_grups_bom": ("grupos.mp4", "Criar grupos de veículos"),
    "setup_drivers_bom": ("motoristas.mp4", "Cadastrar motoristas"),
    "basic_config_fleet_policy_bom": ("politica_frota.mp4", "Criar política de frota"),
    "basic_config_geofences_bom": ("geofences.mp4", "Criar local de interesse (geofence)"),
    "basic_config_checklists_bom": ("checklists.mp4", "Criar checklist"),
}
# Vídeo base da fase de instalação (não é critério do BV, mas é pré-requisito)
VIDEO_VEICULOS = ("veiculos.mp4", "Cadastro de veículos")

CRITERIOS = {
    "Instalação": [("Mais de 90% das instalações concluídas", "instalation_completeness_grade_bom", "INSTALL_COMPLETENESS", "%")],
    "Setup": [
        ("Pelo menos 2 Perfis de Usuário", "setup_user_bom", "raw_num_profiles", "un"),
        ("Pelo menos 2 Grupos de Veículos", "setup_grups_bom", "raw_num_groups", "un"),
        ("Pelo menos 2 Motoristas cadastrados", "setup_drivers_bom", "raw_num_drivers", "un"),
        ("60%+ das viagens com motorista identificado", "setup_driver_identification_bom", "raw_proportion_trips", "%"),
    ],
    "Configuração Básica": [
        ("Limite de velocidade em 60%+ dos veículos", "basic_config_speed_limit_bom", "raw_proportion_speed_limit", "%"),
        ("Pelo menos 2 Regras de Política de Frota", "basic_config_fleet_policy_bom", "raw_num_fleet_policy", "un"),
        ("Pelo menos 2 Geofences", "basic_config_geofences_bom", "raw_num_geofences", "un"),
        ("Pelo menos 2 Checklists", "basic_config_checklists_bom", "raw_num_checklists", "un"),
    ],
}

# Ordem canônica dos gaps (todos os critérios)
GAPS_ORDER = [
    "instalation_completeness_grade_bom", "setup_user_bom", "setup_grups_bom",
    "setup_drivers_bom", "setup_driver_identification_bom", "basic_config_speed_limit_bom",
    "basic_config_fleet_policy_bom", "basic_config_geofences_bom", "basic_config_checklists_bom",
]


# ----------------------------------------------------------------------------
# Conexões
# ----------------------------------------------------------------------------
@st.cache_resource
def _databricks_conn():
    from databricks import sql
    cfg = st.secrets["databricks"]
    host = (cfg.get("server_hostname") or cfg.get("DATABRICKS_HOST") or "")
    host = host.replace("https://", "").replace("http://", "").strip().rstrip("/")
    http_path = cfg.get("http_path")
    if not http_path:
        wid = (cfg.get("DATABRICKS_WAREHOUSE_ID") or "").strip()
        http_path = f"/sql/1.0/warehouses/{wid}"
    token = cfg.get("access_token") or cfg.get("DATABRICKS_TOKEN")
    return sql.connect(server_hostname=host, http_path=http_path, access_token=token)


def run_databricks(query: str) -> pd.DataFrame:
    conn = _databricks_conn()
    with conn.cursor() as cur:
        cur.execute(query)
        cols = [c[0] for c in cur.description]
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=cols)


# ----------------------------------------------------------------------------
# Query
# ----------------------------------------------------------------------------
BV_COLS = [
    "company_id", "company_name", "fleet_id", "csm", "status_da_empresa",
    "basic_value_score", "INSTALL_COMPLETENESS", "data_de_assinatura_do_contrato",
    "semana_basic_value", "activation_heavy_users", "raw_num_heavy_users",
    "instalation_completeness_grade", "setup_grade", "basic_config_grade",
    "raw_num_profiles", "raw_num_groups", "raw_num_drivers",
    "raw_proportion_trips", "raw_proportion_speed_limit",
    "raw_num_fleet_policy", "raw_num_geofences", "raw_num_checklists",
    "instalation_completeness_grade_bom", "setup_user_bom", "setup_grups_bom",
    "setup_drivers_bom", "setup_driver_identification_bom",
    "basic_config_speed_limit_bom", "basic_config_fleet_policy_bom",
    "basic_config_geofences_bom", "basic_config_checklists_bom",
]


def _bv_por_company(company_id: str) -> pd.DataFrame:
    return run_databricks(f"""
        SELECT {", ".join(BV_COLS)}
        FROM gold.customer_success_reports.basic_value
        WHERE company_id = '{company_id}' AND data_atual_flag = true
        LIMIT 1
    """)


def fetch_client(hs_id: str) -> dict:
    """Aceita ID de empresa (HubSpot 0-2) OU ID de negócio (deal 0-3)."""
    hs_id = hs_id.strip()

    bv = _bv_por_company(hs_id)
    company_id = hs_id if not bv.empty else None

    if company_id is None:
        r = run_databricks(f"""
            SELECT MAX(DEAL_ASSOCIATED_COMPANY_ID) AS company_id
            FROM dimensions.dim_client_info
            WHERE DEAL_ID = '{hs_id}' OR DEAL_ASSOCIATED_COMPANY_ID = '{hs_id}'
        """)
        company_id = (r.iloc[0]["company_id"] if not r.empty else None) or None
        if not company_id:
            r2 = run_databricks(f"SELECT MAX(company_id) AS company_id FROM gold.cubo_supply.supply_cube WHERE deal_id = '{hs_id}'")
            company_id = (r2.iloc[0]["company_id"] if not r2.empty else None) or None
        if company_id:
            bv = _bv_por_company(company_id)

    sup = run_databricks(f"""
        SELECT
          MAX(fleet_id)     AS fleet_id,
          MAX(company_name) AS company_name,
          MAX(CASE WHEN instalacao__data_hora_marcada IS NOT NULL
                    OR instalacao__data_dia_marcado IS NOT NULL
                    OR instalacao__entry_date_agendar_instalacao IS NOT NULL
                    OR instalacao__entry_date_aguardando_tecnico_instalar IS NOT NULL
                   THEN 1 ELSE 0 END)                              AS agendado,
          MAX(instalacao__data_dia_marcado)                        AS data_marcada,
          MAX(CASE WHEN instalacao__entry_date_instalado IS NOT NULL
                    OR instalacao__data_realizada IS NOT NULL
                   THEN 1 ELSE 0 END)                              AS instalado_flag,
          MAX(instalacao__cliente_nome)                            AS cliente_nome,
          MAX(instalacao__cliente_telefone)                        AS cliente_telefone,
          MAX(instalacao__cliente_email)                           AS cliente_email
        FROM gold.cubo_supply.supply_cube
        WHERE company_id = '{company_id or ''}' OR deal_id = '{hs_id}'
    """)
    sup = sup.iloc[0].to_dict() if not sup.empty else {}

    bv = bv.iloc[0].to_dict() if not bv.empty else {}
    return {"hs_id": hs_id, "company_id": company_id, "supply": sup, "bv": bv}


# ----------------------------------------------------------------------------
# Regras de negócio
# ----------------------------------------------------------------------------
def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def instalacao_nao_iniciada(dados):
    sup, bv = dados["supply"], dados["bv"]
    agendado = (sup.get("agendado") or 0) == 1
    instalado = (sup.get("instalado_flag") or 0) == 1
    completeness = _to_float(bv.get("INSTALL_COMPLETENESS")) or 0
    return not agendado and not (instalado or completeness > 0)


def tem_usuario_ativo(bv):
    h = _to_float(bv.get("activation_heavy_users")) or 0
    r = _to_float(bv.get("raw_num_heavy_users")) or 0
    return h > 0 or r > 0


def dias_de_jornada(bv):
    d = bv.get("data_de_assinatura_do_contrato")
    if not d:
        return None
    try:
        d = pd.to_datetime(d, utc=True).tz_localize(None)
    except Exception:
        d = pd.to_datetime(d)
    return int((pd.Timestamp.now().normalize() - d.normalize()).days)


def fase_atual(dias):
    if dias is None:
        return 0
    for i, f in enumerate(FASES):
        if dias <= f["fim"]:
            return i
    return len(FASES) - 1


def classificar_nota(score):
    s = _to_float(score)
    if s is None:
        return "Sem dados", "#9AA0A6"
    if s >= 3:
        return "Saudável", "#1DB954"
    if s >= 2:
        return "Atenção", "#F5A623"
    return "Crítico", "#E5484D"


def pendentes_da_fase(i, bv):
    """Retorna [(col, texto)] dos critérios da fase ainda não atingidos."""
    return [(c, COL_TEXT[c]) for c in PHASE_CRITERIA.get(i, []) if not bool(bv.get(c))]


def status_fase(i, dias, bv):
    """(rótulo, emoji, cor) considerando prazo x execução."""
    f = FASES[i]
    if i == 3:
        s = _to_float(bv.get("basic_value_score"))
        pend = not (s is not None and s >= META_BASIC_VALUE)
    else:
        pend = len(pendentes_da_fase(i, bv)) > 0

    if not pend:
        return "concluída", "✅", "#1DB954"
    if dias is not None and dias > f["fim"]:
        return "não executado no tempo", "⚠️", "#E5484D"
    if dias is not None and f["ini"] <= dias <= f["fim"]:
        return "fase atual", "🟡", "#F5A623"
    return "a seguir", "⚪", "#9AA0A6"


def gaps_abertos(bv):
    return [(c, COL_TEXT[c]) for c in GAPS_ORDER if not bool(bv.get(c))]


# ----------------------------------------------------------------------------
# WhatsApp / vídeos
# ----------------------------------------------------------------------------
def wa_link(phone, text):
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return None
    if not digits.startswith("55") and len(digits) <= 11:
        digits = "55" + digits
    return f"https://wa.me/{digits}?text={quote(text)}"


def video_url(arquivo, base_url):
    # 1) URL explícita em st.secrets [videos] (chave = nome do arquivo ou caminho relativo)
    try:
        v = dict(st.secrets.get("videos", {}))
        for chave in (arquivo, os.path.basename(arquivo)):
            if chave in v and str(v[chave]).strip():
                return str(v[chave]).strip()
    except Exception:
        pass
    # 2) URL base + caminho relativo
    base = (base_url or "").strip().rstrip("/")
    return f"{base}/{arquivo}" if base else None


def video_path(arquivo):
    p = os.path.join(VIDEOS_DIR, arquivo)
    return p if os.path.exists(p) else None


def linha_gap(texto, col, base_url):
    """Bullet do gap, com link do vídeo quando disponível."""
    vid = VIDEO_MAP.get(col)
    if vid:
        url = video_url(vid[0], base_url)
        if url:
            return f"• {texto}. Passo a passo em vídeo: {url}"
    return f"• {texto}"


# ----------------------------------------------------------------------------
# Mensagens
# ----------------------------------------------------------------------------
def msg_kickoff(nome, empresa, analista, link=None, incluir_instalacao=False):
    saud = f"Oi, {nome}, tudo bem?" if nome else "Oi, tudo bem?"
    alvo = f"a {empresa}" if empresa else "a sua operação"
    abertura = (
        f"{saud}\n\n"
        f"Sou o {analista}, da Cobli, e vou ser seu ponto de contato de treinamento "
        f"pelos próximos 3 meses. Minha missão é simples: deixar {alvo} rodando redonda "
        f"na plataforma e garantir que vocês sintam o valor logo nas primeiras semanas."
    )
    passos = []
    if link:
        passos.append(f"*Seu acesso ao painel*\nÉ só concluir o cadastro por aqui: {link}")
    if incluir_instalacao:
        passos.append(
            "*Agendamento da instalação*\n"
            "Me manda estas informações que eu já organizo tudo com o time técnico:\n"
            "• 2 opções de data e horário\n• Endereço completo da instalação\n"
            "• Nome e telefone do responsável no local\n• Placas dos veículos"
        )
    corpo = ""
    if len(passos) > 1:
        corpo = "Para começar com o pé direito, dois passos rápidos:\n\n" + "\n\n".join(
            f"{i + 1}️⃣ {p}" for i, p in enumerate(passos))
    elif len(passos) == 1:
        corpo = "Para começar, um passo rápido:\n\n" + passos[0]
    fecho = ("Feito isso, o restante fica comigo e eu te mantenho por dentro de cada etapa. "
             "Qualquer dúvida no caminho, é só me chamar por aqui. 😊")
    partes = [abertura] + ([corpo] if corpo else []) + [fecho]
    return "\n\n".join(partes)


def msg_gaps(nome, empresa, gaps, score, base_url):
    saud = f"Oi, {nome}, tudo bem?" if nome else "Oi, tudo bem?"
    if not gaps:
        s = _to_float(score)
        nota = f" e o Basic Value em {s:.1f}".replace(".", ",") if s is not None else ""
        return (f"{saud}\n\nPassando para comemorar: {('a ' + empresa) if empresa else 'a sua frota'} "
                f"já está com a configuração completa{nota}. É exatamente o patamar que a gente busca no "
                f"onboarding. Sigo por aqui para o que precisar. 🎉")
    itens = "\n".join(linha_gap(t, c, base_url) for c, t in gaps)
    return (f"{saud}\n\nDei uma olhada na conta de vocês e separei o que ainda falta para a frota atingir "
            f"a configuração ideal da Cobli. Deixei o vídeo do passo a passo em cada item:\n\n{itens}\n\n"
            f"Consigo te acompanhar em cada um, com calma. Quer que a gente resolva os primeiros ainda essa "
            f"semana? Me diz o melhor horário.")


def msg_fase(i, nome, empresa, bv, base_url):
    saud = f"Oi, {nome}, tudo bem?" if nome else "Oi, tudo bem?"
    emp = empresa or "a sua frota"
    intro = [
        f"Boas-vindas à Cobli! Nas próximas semanas vou te acompanhar para deixar {emp} rodando redonda. "
        f"O primeiro passo é a instalação dos equipamentos e o seu acesso ao painel.",
        f"Com os equipamentos instalados, bora deixar a plataforma com a cara da sua operação. "
        f"Nesta etapa a gente organiza usuários, grupos e motoristas.",
        f"Agora vem a parte que mais gera resultado no dia a dia: limite de velocidade, políticas de frota, "
        f"locais de interesse e checklists.",
        f"Estamos fechando seus primeiros 90 dias na Cobli. Bora revisar juntos os últimos ajustes para "
        f"garantir a configuração completa.",
    ][i]

    if i == 3:
        s = _to_float(bv.get("basic_value_score"))
        pend = gaps_abertos(bv)
        if s is not None and s >= META_BASIC_VALUE:
            return (f"{saud}\n\n{intro}\n\nSua frota já está em {s:.1f} de Basic Value".replace(".", ",", 1)
                    + ", acima da meta de 3. Parabéns pelo trabalho! Sigo à disposição para o que vier. 🎉")
        alvo = f"em {s:.1f} de Basic Value".replace(".", ",") if s is not None else "quase lá"
        itens = "\n".join(linha_gap(t, c, base_url) for c, t in pend)
        return (f"{saud}\n\n{intro}\n\nSua frota está {alvo} e a meta é chegar a 3. Faltam poucos ajustes:\n\n"
                f"{itens}\n\nBora fechar esses itens juntos antes de encerrar o onboarding?")

    pend = pendentes_da_fase(i, bv)
    if not pend:
        return f"{saud}\n\n{intro}\n\nPor aqui está tudo certo nesta fase. Seguimos para a próxima etapa. 😊"
    itens = "\n".join(linha_gap(t, c, base_url) for c, t in pend)
    return (f"{saud}\n\n{intro}\n\nPara avançar nesta fase, faltam estes passos (deixei o vídeo de cada um):\n\n"
            f"{itens}\n\nConsigo te guiar item a item. Qual o melhor horário para a gente ver isso?")


# ----------------------------------------------------------------------------
# UI helpers
# ----------------------------------------------------------------------------
def render_timeline(dias, bv):
    cols = st.columns(len(FASES))
    for i, (col, f) in enumerate(zip(cols, FASES)):
        rot, emoji, cor = status_fase(i, dias, bv)
        with col:
            st.markdown(
                f"<div style='border-top:4px solid {cor};padding-top:8px'>"
                f"<b>{f['emoji']} Fase {i + 1}</b><br>{f['nome']}<br>"
                f"<span style='color:{cor}'>Dias {f['ini']}–{f['fim']}<br>{emoji} {rot}</span></div>",
                unsafe_allow_html=True,
            )


def linha_criterio(label, ok, valor, unidade):
    icon = "✅" if ok else "❌"
    v = _to_float(valor)
    vtxt = "sem dado" if v is None else (f"{v:.0f}%" if unidade == "%" else f"{v:.0f}")
    st.markdown(f"{icon}  {label}  ·  **{vtxt}**")


def botao_whatsapp(texto, telefone, key):
    url = wa_link(telefone, texto)
    if url:
        st.link_button("📲 Enviar no WhatsApp", url, use_container_width=True)
    else:
        st.caption("Informe o telefone do cliente na barra lateral para gerar o link de envio.")


def mostrar_videos(cols, base_url, prefix=""):
    """Players + download dos vídeos dos critérios em `cols`.
    `prefix` garante chaves únicas quando a mesma seção se repete em abas diferentes."""
    vids = [(c, VIDEO_MAP[c]) for c in cols if c in VIDEO_MAP]
    if not vids:
        return
    st.markdown("**Vídeos para anexar no WhatsApp**")
    st.caption("O wa.me abre a conversa com o texto pronto. Anexe os vídeos abaixo (baixe ou arraste). "
               "Se você hospedar a pasta de vídeos, os links já entram no texto automaticamente.")
    for col, (arquivo, titulo) in vids:
        p = video_path(arquivo)
        nome_arq = os.path.basename(arquivo)
        st.markdown(f"*{titulo}*")
        if p:
            st.video(p)
            with open(p, "rb") as fh:
                st.download_button(f"Baixar · {titulo}", fh, file_name=nome_arq, key=f"dl_{prefix}_{col}")
        elif video_url(arquivo, base_url):
            st.write(video_url(arquivo, base_url))
        else:
            st.caption(f"({arquivo} não encontrado)")


# ----------------------------------------------------------------------------
# App
# ----------------------------------------------------------------------------
st.title("🚚 Jornada de Onboarding · Cobli")
st.caption("Do kickoff aos 90 dias. Meta: Basic Value ≥ 3. A partir do ID da empresa no HubSpot.")

with st.sidebar:
    st.header("Dados do cliente")
    hs_id = st.text_input(
        "ID da empresa (HubSpot)", placeholder="ex.: 34318770456",
        help="Número no fim da URL do registro de Empresa (.../record/0-2/ESTE_NÚMERO). Também aceita ID de negócio.",
    )
    nome_cliente = st.text_input("Nome do contato", placeholder="ex.: João")
    telefone = st.text_input("Telefone do cliente (WhatsApp)", placeholder="ex.: 11987654321")
    analista = st.text_input("Seu nome (analista)", value=ANALISTA_PADRAO)
    fez_kickoff = st.radio("Já fez o kickoff via mensagem?", ["Ainda não", "Sim, já fiz"])
    base_url = st.text_input("URL base dos vídeos (opcional)", value=VIDEOS_BASE_URL,
                             help="Se hospedar a pasta videos/ (ex.: no GitHub), cole a URL base para os links entrarem nas mensagens.")
    buscar = st.button("Analisar cliente", type="primary", use_container_width=True)

if not buscar:
    st.info("Cole o ID da empresa (HubSpot) na barra lateral e clique em **Analisar cliente**.")
    st.stop()

if not hs_id.strip():
    st.error("Informe o ID da empresa.")
    st.stop()

try:
    dados = fetch_client(hs_id)
except Exception as e:
    st.error(f"Erro ao consultar o Databricks: {e}")
    st.stop()

bv, sup = dados["bv"], dados["supply"]
empresa = (bv.get("company_name") or sup.get("company_name") or "").strip()
nome_final = nome_cliente.strip() or (sup.get("cliente_nome") or "").strip()
analista_final = analista.strip() or ANALISTA_PADRAO
telefone_final = telefone.strip() or (sup.get("cliente_telefone") or "")

if not dados["company_id"]:
    st.warning("Não encontrei essa empresa no Databricks. Confira se é o ID da Empresa no HubSpot (.../record/0-2/NÚMERO).")

score = bv.get("basic_value_score")
dias = dias_de_jornada(bv)
idx = fase_atual(dias)
label, cor = classificar_nota(score)
gaps = gaps_abertos(bv) if bv else []
usuario_ativo = tem_usuario_ativo(bv)

# ---- Cabeçalho ----
st.subheader(empresa or f"Empresa {dados['hs_id']}")
c1, c2, c3, c4 = st.columns(4)
sv = _to_float(score)
c1.metric("Basic Value", f"{sv:.2f}" if sv is not None else "—", f"meta {META_BASIC_VALUE:.0f}")
c1.markdown(f"<span style='color:{cor};font-weight:600'>{label}</span>", unsafe_allow_html=True)
c2.metric("Dia da jornada", f"{dias}" if dias is not None else "—", f"de {JORNADA_DIAS}")
c3.metric("Dias restantes", f"{max(JORNADA_DIAS - dias, 0)}" if dias is not None else "—")
c4.metric("Usuário com acesso", "Sim" if usuario_ativo else "Não")
if dias is not None:
    st.progress(min(max(dias, 0) / JORNADA_DIAS, 1.0))

st.divider()

tab_jornada, tab_msg, tab_fases = st.tabs(
    ["📍 Linha do tempo & diagnóstico", "💬 Kickoff / próximos passos", "🗓️ Comunicações por fase"]
)

# ============================ TAB 1 ============================
with tab_jornada:
    st.markdown("### Linha do tempo")
    render_timeline(dias, bv)
    atrasadas = [i for i in range(len(FASES)) if status_fase(i, dias, bv)[0] == "não executado no tempo"]
    if atrasadas:
        nomes = ", ".join(f"Fase {i + 1} ({FASES[i]['nome']})" for i in atrasadas)
        st.error(f"⚠️ Fora do prazo: {nomes}. Há critérios que deveriam estar prontos e ainda estão pendentes.")
    st.markdown(f"**Fase atual:** {FASES[idx]['emoji']} {FASES[idx]['nome']}")
    st.divider()

    st.markdown("### O que o cliente já fez")
    if bv:
        grade_map = {"Instalação": bv.get("instalation_completeness_grade"),
                     "Setup": bv.get("setup_grade"),
                     "Configuração Básica": bv.get("basic_config_grade")}
        cols = st.columns(3)
        for col, (pilar, criterios) in zip(cols, CRITERIOS.items()):
            with col:
                g = grade_map.get(pilar)
                st.markdown(f"#### {pilar}")
                st.markdown(f"Nota do pilar: **{g if g is not None else '—'} / 4**")
                for label_c, bom_col, raw_col, un in criterios:
                    linha_criterio(label_c, bool(bv.get(bom_col)), bv.get(raw_col), un)
        feitos = len(GAPS_ORDER) - len(gaps)
        st.info(f"Critérios concluídos: **{feitos} de {len(GAPS_ORDER)}**. Faltam **{len(gaps)}**.")
    else:
        st.info("Sem registro de Basic Value na semana atual para esta empresa.")

# ============================ TAB 2 ============================
with tab_msg:
    if fez_kickoff == "Ainda não":
        st.markdown("### Mensagem de kickoff")
        link = None
        if not usuario_ativo:
            st.warning("Este cliente ainda **não tem usuário com acesso** ao painel. Cole o link de convite para incluí-lo na mensagem.")
            link = (st.text_input("Link de acesso do cliente", placeholder="https://cadastro.cobli.co/invites/XXXXXXXX").strip() or None)
        else:
            st.success("Cliente já tem usuário com acesso ao painel. O link de convite não é necessário.")
        msg = msg_kickoff(nome_final, empresa, analista_final, link=link, incluir_instalacao=instalacao_nao_iniciada(dados))
        st.text_area("kickoff_msg", msg, height=max(340, len(msg) // 2), label_visibility="collapsed")
        botao_whatsapp(msg, telefone_final, "wa_kick")
    else:
        st.markdown("### Próximos passos (features que faltam)")
        if not bv:
            st.info("Sem Basic Value para gerar a lista de pendências.")
        else:
            msg = msg_gaps(nome_final, empresa, gaps, score, base_url)
            st.text_area("gaps_msg", msg, height=max(300, len(msg) // 2), label_visibility="collapsed")
            botao_whatsapp(msg, telefone_final, "wa_gaps")
            if gaps:
                st.caption(f"{len(gaps)} feature(s) pendente(s) no Basic Value.")
                st.divider()
                mostrar_videos([c for c, _ in gaps], base_url, prefix="gaps")

# ============================ TAB 3 ============================
with tab_fases:
    st.markdown("### Comunicações da jornada de 90 dias")
    st.caption("Cada fase traz a comunicação com base no Basic Value: só entram os passos ainda pendentes, com o vídeo de cada um.")
    for i, f in enumerate(FASES):
        rot, emoji, _ = status_fase(i, dias, bv)
        with st.expander(f"{f['emoji']} Fase {i + 1}: {f['nome']} · {emoji} {rot}", expanded=(i == idx)):
            texto = msg_fase(i, nome_final, empresa, bv, base_url)
            st.text_area(f"fase_{i}", texto, height=max(220, len(texto) // 2), label_visibility="collapsed")
            botao_whatsapp(texto, telefone_final, f"wa_fase_{i}")
            pend_cols = [c for c, _ in (gaps_abertos(bv) if i == 3 else pendentes_da_fase(i, bv))]
            mostrar_videos(pend_cols, base_url, prefix=f"fase{i}")
