"""
Cobli - Onboarding Tech Touch | Jornada de 90 dias
===================================================
Recebe um Deal ID do HubSpot, posiciona o cliente na jornada de 90 dias
(meta: Basic Value >= 3), mostra o que ele já fez, e monta as comunicações
de WhatsApp de cada fase.

Como rodar:
    pip install -r requirements.txt
    streamlit run app.py

Credenciais: preencher .streamlit/secrets.toml (ver README).
"""

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Cobli · Jornada de Onboarding", page_icon="🚚", layout="wide")

ANALISTA_PADRAO = "Igor"
INVITE_BASE_URL = "https://cadastro.cobli.co/invites/{}"
META_BASIC_VALUE = 3.0
JORNADA_DIAS = 90

# Fases da jornada (por dia desde a assinatura do contrato)
FASES = [
    {"nome": "Boas-vindas & Instalação", "ini": 0, "fim": 15, "emoji": "📦"},
    {"nome": "Configuração inicial (Setup)", "ini": 16, "fim": 45, "emoji": "⚙️"},
    {"nome": "Segurança & gestão", "ini": 46, "fim": 75, "emoji": "🛡️"},
    {"nome": "Consolidação & valor", "ini": 76, "fim": 90, "emoji": "🎯"},
]

# Critérios do Basic Value: (label, coluna booleana _bom, coluna do valor bruto, unidade)
CRITERIOS = {
    "Instalação": [
        ("Mais de 90% das instalações concluídas", "instalation_completeness_grade_bom", "INSTALL_COMPLETENESS", "%"),
    ],
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

# Gaps na ordem da jornada: (coluna _bom, texto de ação para o cliente)
GAPS = [
    ("instalation_completeness_grade_bom", "Concluir a instalação dos equipamentos (mais de 90% da frota ativa)"),
    ("setup_user_bom", "Criar pelo menos 2 perfis de usuário para a sua equipe"),
    ("setup_grups_bom", "Organizar os veículos em pelo menos 2 grupos"),
    ("setup_drivers_bom", "Cadastrar pelo menos 2 motoristas"),
    ("setup_driver_identification_bom", "Identificar o motorista em pelo menos 60% das viagens"),
    ("basic_config_speed_limit_bom", "Configurar o limite de velocidade em pelo menos 60% dos veículos"),
    ("basic_config_fleet_policy_bom", "Criar pelo menos 2 regras de política de frota"),
    ("basic_config_geofences_bom", "Criar pelo menos 2 geofences (cercas virtuais)"),
    ("basic_config_checklists_bom", "Criar pelo menos 2 checklists"),
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


def get_invite_link(email: str):
    """Consulta o severino (Postgres) e retorna (link, row) ou (None, None).
    Só funciona dentro da rede interna da Cobli (VPN/VPC)."""
    import psycopg2
    cfg = st.secrets["severino"]
    conn = psycopg2.connect(
        dbname=cfg["dbname"], user=cfg["user"],
        password=cfg["password"], host=cfg["host"], connect_timeout=8,
    )
    try:
        df = pd.read_sql(
            "select * from invites where email_address = %s order by creation_date desc",
            conn, params=(email,),
        )
    finally:
        conn.close()
    if df.empty:
        return None, None
    row = df.iloc[0]
    return INVITE_BASE_URL.format(row["token"]), row


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


def fetch_deal(deal_id: str) -> dict:
    deal_id = deal_id.strip()

    sup = run_databricks(f"""
        SELECT
          MAX(company_id)   AS company_id,
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
        WHERE deal_id = '{deal_id}'
    """)
    sup = sup.iloc[0].to_dict() if not sup.empty else {}

    company_id = sup.get("company_id")
    if not company_id:
        c = run_databricks(f"""
            SELECT MAX(associated_company_id) AS company_id
            FROM gold.cubo_contratos.fct_contract_products
            WHERE deal_id = '{deal_id}' OR ticket_associated_deal_id = '{deal_id}'
        """)
        company_id = c.iloc[0]["company_id"] if not c.empty else None

    bv = pd.DataFrame()
    if company_id:
        bv = run_databricks(f"""
            SELECT {", ".join(BV_COLS)}
            FROM gold.customer_success_reports.basic_value
            WHERE company_id = '{company_id}' AND data_atual_flag = true
            LIMIT 1
        """)
    bv = bv.iloc[0].to_dict() if not bv.empty else {}

    return {"deal_id": deal_id, "company_id": company_id, "supply": sup, "bv": bv}


# ----------------------------------------------------------------------------
# Regras de negócio
# ----------------------------------------------------------------------------
def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def instalacao_nao_iniciada(dados: dict) -> bool:
    sup, bv = dados["supply"], dados["bv"]
    agendado = (sup.get("agendado") or 0) == 1
    instalado = (sup.get("instalado_flag") or 0) == 1
    completeness = _to_float(bv.get("INSTALL_COMPLETENESS")) or 0
    return not agendado and not (instalado or completeness > 0)


def tem_usuario_ativo(bv: dict) -> bool:
    h = _to_float(bv.get("activation_heavy_users")) or 0
    r = _to_float(bv.get("raw_num_heavy_users")) or 0
    return h > 0 or r > 0


def dias_de_jornada(bv: dict):
    d = bv.get("data_de_assinatura_do_contrato")
    if not d:
        return None
    try:
        d = pd.to_datetime(d, utc=True).tz_localize(None)
    except Exception:
        d = pd.to_datetime(d)
    hoje = pd.Timestamp.now().normalize()
    return int((hoje - d.normalize()).days)


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


def gaps_abertos(bv: dict):
    """Lista de textos de ação dos critérios ainda não atingidos."""
    return [txt for col, txt in GAPS if not bool(bv.get(col))]


# ----------------------------------------------------------------------------
# Mensagens de WhatsApp
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
        passos.append("*Seu acesso ao painel*\n" f"É só concluir o cadastro por aqui: {link}")
    if incluir_instalacao:
        passos.append(
            "*Agendamento da instalação*\n"
            "Me manda estas informações que eu já organizo tudo com o time técnico:\n"
            "• 2 opções de data e horário\n"
            "• Endereço completo da instalação\n"
            "• Nome e telefone do responsável no local\n"
            "• Placas dos veículos"
        )
    corpo = ""
    if len(passos) > 1:
        corpo = "Para começar com o pé direito, dois passos rápidos:\n\n"
        corpo += "\n\n".join(f"{i + 1}️⃣ {p}" for i, p in enumerate(passos))
    elif len(passos) == 1:
        corpo = "Para começar, um passo rápido:\n\n" + passos[0]
    fecho = (
        "Feito isso, o restante fica comigo e eu te mantenho por dentro de cada etapa. "
        "Qualquer dúvida no caminho, é só me chamar por aqui. 😊"
    )
    partes = [abertura] + ([corpo] if corpo else []) + [fecho]
    return "\n\n".join(partes)


def msg_gaps(nome, empresa, analista, gaps, score):
    saud = f"Oi, {nome}, tudo bem?" if nome else "Oi, tudo bem?"
    if not gaps:
        return (
            f"{saud}\n\n"
            f"Passando só para comemorar: {('a ' + empresa) if empresa else 'a sua frota'} "
            f"já está com a configuração completa e o Basic Value em {_to_float(score):.1f}. "
            f"É exatamente o patamar que a gente busca no onboarding. Sigo por aqui para o que precisar. 🎉"
        )
    itens = "\n".join(f"• {g}" for g in gaps)
    return (
        f"{saud}\n\n"
        f"Dei uma olhada na conta de vocês e separei o que ainda falta para a frota atingir "
        f"a configuração ideal da Cobli. Cada ponto leva poucos minutos:\n\n"
        f"{itens}\n\n"
        f"Consigo te mostrar como fazer cada um, com calma. Quer que a gente resolva os "
        f"primeiros ainda essa semana? Me diz o melhor horário que eu te acompanho."
    )


def msg_fase(indice, nome, empresa, score):
    saud = f"Oi, {nome}, tudo bem?" if nome else "Oi, tudo bem?"
    emp = empresa or "a sua frota"
    s = _to_float(score)
    textos = [
        # Fase 1
        f"{saud}\n\nBoas-vindas à Cobli! Nas próximas semanas vou te acompanhar de perto "
        f"para deixar {emp} rodando redonda. O primeiro passo é a instalação dos equipamentos "
        f"e o seu acesso ao painel. Me avisa a melhor data que eu já organizo tudo com o time técnico.",
        # Fase 2
        f"{saud}\n\nCom os equipamentos instalados, bora deixar a plataforma com a cara da sua "
        f"operação. Nesta etapa a gente cria os usuários da equipe, organiza os veículos em grupos "
        f"e cadastra os motoristas. Isso já te dá visão de quem dirige o quê. Quer marcar 20 minutos "
        f"comigo para configurarmos juntos?",
        # Fase 3
        f"{saud}\n\nAgora vem a parte que mais gera resultado no dia a dia: configurar limite de "
        f"velocidade, regras de política de frota, geofences e checklists. É o que transforma os "
        f"dados da frota em ação. Posso te guiar item a item essa semana, no seu ritmo.",
        # Fase 4
        f"{saud}\n\nEstamos fechando seus primeiros 90 dias na Cobli. Sua frota já está em "
        f"{s:.1f} de Basic Value" + (" e a meta é chegar a 3." if s is not None and s < 3 else ", acima da meta de 3.")
        + " Bora revisar juntos os últimos ajustes e deixar tudo redondo antes de encerrar o onboarding?",
    ]
    return textos[indice]


# ----------------------------------------------------------------------------
# UI helpers
# ----------------------------------------------------------------------------
def render_timeline(dias, idx_atual):
    dias_show = "—" if dias is None else max(dias, 0)
    cols = st.columns(len(FASES))
    for i, (col, f) in enumerate(zip(cols, FASES)):
        if i < idx_atual:
            estado, cor = "✅ concluída", "#1DB954"
        elif i == idx_atual:
            estado, cor = "🟡 fase atual", "#F5A623"
        else:
            estado, cor = "⚪ a seguir", "#9AA0A6"
        with col:
            st.markdown(
                f"<div style='border-top:4px solid {cor};padding-top:8px'>"
                f"<b>{f['emoji']} Fase {i + 1}</b><br>{f['nome']}<br>"
                f"<span style='color:{cor}'>Dias {f['ini']}–{f['fim']} · {estado}</span></div>",
                unsafe_allow_html=True,
            )


def linha_criterio(label, ok, valor, unidade):
    icon = "✅" if ok else "❌"
    v = _to_float(valor)
    vtxt = "sem dado" if v is None else (f"{v:.0f}%" if unidade == "%" else f"{v:.0f}")
    st.markdown(f"{icon}  {label}  ·  **{vtxt}**")


# ----------------------------------------------------------------------------
# App
# ----------------------------------------------------------------------------
st.title("🚚 Jornada de Onboarding · Cobli")
st.caption("Do kickoff aos 90 dias. Meta: Basic Value ≥ 3. A partir do Deal ID do HubSpot.")

with st.sidebar:
    st.header("Dados do cliente")
    deal_id = st.text_input("Deal ID (HubSpot)", placeholder="ex.: 47596831034")
    nome_cliente = st.text_input("Nome do contato", placeholder="ex.: João")
    analista = st.text_input("Seu nome (analista)", value=ANALISTA_PADRAO)
    buscar = st.button("Analisar cliente", type="primary", use_container_width=True)

if not buscar:
    st.info("Preencha o Deal ID na barra lateral e clique em **Analisar cliente**.")
    st.stop()

if not deal_id.strip():
    st.error("Informe o Deal ID.")
    st.stop()

try:
    dados = fetch_deal(deal_id)
except Exception as e:
    st.error(f"Erro ao consultar o Databricks: {e}")
    st.stop()

bv, sup = dados["bv"], dados["supply"]
empresa = (bv.get("company_name") or sup.get("company_name") or "").strip()
nome_final = nome_cliente.strip() or (sup.get("cliente_nome") or "").strip()
analista_final = analista.strip() or ANALISTA_PADRAO

if not dados["company_id"]:
    st.warning("Não encontrei empresa associada a este Deal ID no Databricks. Confira o número.")

score = bv.get("basic_value_score")
dias = dias_de_jornada(bv)
idx = fase_atual(dias)
label, cor = classificar_nota(score)
gaps = gaps_abertos(bv) if bv else []
usuario_ativo = tem_usuario_ativo(bv)

# ---- Cabeçalho ----
st.subheader(empresa or f"Deal {dados['deal_id']}")
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

# ============================ TAB 1: Jornada ============================
with tab_jornada:
    st.markdown("### Linha do tempo")
    render_timeline(dias, idx)
    st.markdown(f"**Fase atual:** {FASES[idx]['emoji']} {FASES[idx]['nome']}")
    st.divider()

    st.markdown("### O que o cliente já fez")
    if bv:
        grade_map = {
            "Instalação": bv.get("instalation_completeness_grade"),
            "Setup": bv.get("setup_grade"),
            "Configuração Básica": bv.get("basic_config_grade"),
        }
        cols = st.columns(3)
        for col, (pilar, criterios) in zip(cols, CRITERIOS.items()):
            with col:
                g = grade_map.get(pilar)
                st.markdown(f"#### {pilar}")
                st.markdown(f"Nota do pilar: **{g if g is not None else '—'} / 4**")
                for label_c, bom_col, raw_col, un in criterios:
                    linha_criterio(label_c, bool(bv.get(bom_col)), bv.get(raw_col), un)
        feitos = len(GAPS) - len(gaps)
        st.info(f"Critérios concluídos: **{feitos} de {len(GAPS)}**. Faltam **{len(gaps)}** para a frota redonda.")
    else:
        st.info("Sem registro de Basic Value na semana atual para esta empresa.")

# ============================ TAB 2: Mensagens ============================
with tab_msg:
    st.markdown("### Este cliente já fez o kickoff via mensagem?")
    fez_kickoff = st.radio(
        "kickoff", ["Ainda não", "Sim, já fiz"], horizontal=True, label_visibility="collapsed"
    )

    if fez_kickoff == "Ainda não":
        st.markdown("#### Mensagem de kickoff")
        link = None
        if not usuario_ativo:
            st.warning("Este cliente ainda **não tem usuário com acesso** ao painel. Cole o link de convite abaixo para incluí-lo na mensagem.")
            link_input = st.text_input(
                "Link de acesso do cliente",
                placeholder="https://cadastro.cobli.co/invites/XXXXXXXX",
                help="Gere o link no severino/cadastro (dentro da rede da Cobli) e cole aqui.",
            )
            link = link_input.strip() or None
        else:
            st.success("Cliente já tem usuário com acesso ao painel. O link de convite não é necessário.")

        msg = msg_kickoff(
            nome_final, empresa, analista_final,
            link=link, incluir_instalacao=instalacao_nao_iniciada(dados),
        )
        st.text_area("kickoff_msg", msg, height=max(340, len(msg) // 2), label_visibility="collapsed")
    else:
        st.markdown("#### Mensagem de próximos passos (features que faltam)")
        if not bv:
            st.info("Sem Basic Value para gerar a lista de pendências.")
        else:
            msg = msg_gaps(nome_final, empresa, analista_final, gaps, score)
            st.text_area("gaps_msg", msg, height=max(300, len(msg) // 2), label_visibility="collapsed")
            if gaps:
                st.caption(f"{len(gaps)} feature(s) pendente(s) com base no Basic Value atual.")

# ============================ TAB 3: Comunicações por fase ============================
with tab_fases:
    st.markdown("### Comunicações da jornada de 90 dias")
    st.caption("Uma mensagem por fase. A fase atual já vem aberta. Revise e copie quando for a hora de cada uma.")
    for i, f in enumerate(FASES):
        marca = " · fase atual" if i == idx else ""
        with st.expander(f"{f['emoji']} Fase {i + 1}: {f['nome']} (dias {f['ini']}–{f['fim']}){marca}", expanded=(i == idx)):
            texto = msg_fase(i, nome_final, empresa, score)
            st.text_area(f"fase_{i}", texto, height=max(220, len(texto) // 2), label_visibility="collapsed")
