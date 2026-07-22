"""
Cobli - Onboarding Tech Touch | Assistente de Kickoff
=====================================================
Recebe um Deal ID do HubSpot, analisa o Basic Value da frota no Databricks,
checa/gera o link de acesso ao painel (severino) e monta as mensagens de
WhatsApp de kickoff prontas para copiar.

Como rodar:
    pip install -r requirements.txt
    streamlit run app.py

Credenciais: preencher .streamlit/secrets.toml (ver README).
"""

import re
import pandas as pd
import streamlit as st

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
st.set_page_config(page_title="Cobli · Kickoff Onboarding", page_icon="🚚", layout="wide")

ANALISTA_PADRAO = "Igor"
INVITE_BASE_URL = "https://cadastro.cobli.co/invites/{}"

# Regras do Basic Value (thresholds por critério)
CRITERIOS = {
    "Instalação": [
        ("Mais de 90% das instalações concluídas", "pct_instalation_bom", "INSTALL_COMPLETENESS", "%"),
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


# ----------------------------------------------------------------------------
# Conexões
# ----------------------------------------------------------------------------
@st.cache_resource
def _databricks_conn():
    from databricks import sql
    cfg = st.secrets["databricks"]
    return sql.connect(
        server_hostname=cfg["server_hostname"],
        http_path=cfg["http_path"],
        access_token=cfg["access_token"],
    )


def run_databricks(query: str) -> pd.DataFrame:
    conn = _databricks_conn()
    with conn.cursor() as cur:
        cur.execute(query)
        cols = [c[0] for c in cur.description]
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=cols)


def get_invite_link(email: str):
    """Consulta o severino (Postgres) e retorna (link, row) ou (None, None)."""
    import psycopg2
    cfg = st.secrets["severino"]
    conn = psycopg2.connect(
        dbname=cfg["dbname"], user=cfg["user"],
        password=cfg["password"], host=cfg["host"],
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
# Queries Databricks
# ----------------------------------------------------------------------------
def fetch_deal(deal_id: str) -> dict:
    deal_id = deal_id.strip()

    # 1) Instalação + empresa (âncora no supply_cube)
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
          MAX(instalacao__data_realizada)                          AS data_realizada,
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

    # Fallback: mapear deal -> empresa pelos contratos
    if not company_id:
        c = run_databricks(f"""
            SELECT MAX(associated_company_id) AS company_id
            FROM gold.cubo_contratos.fct_contract_products
            WHERE deal_id = '{deal_id}' OR ticket_associated_deal_id = '{deal_id}'
        """)
        company_id = c.iloc[0]["company_id"] if not c.empty else None

    # 2) Basic Value da semana atual
    bv = pd.DataFrame()
    if company_id:
        cols = ", ".join([
            "company_id", "company_name", "fleet_id", "csm", "status_da_empresa",
            "basic_value_score", "INSTALL_COMPLETENESS",
            "instalation_completeness_grade", "setup_grade", "basic_config_grade",
            "raw_num_profiles", "raw_num_groups", "raw_num_drivers",
            "raw_proportion_trips", "raw_proportion_speed_limit",
            "raw_num_fleet_policy", "raw_num_geofences", "raw_num_checklists",
            "pct_instalation_bom", "setup_user_bom", "setup_grups_bom",
            "setup_drivers_bom", "setup_driver_identification_bom",
            "basic_config_speed_limit_bom", "basic_config_fleet_policy_bom",
            "basic_config_geofences_bom", "basic_config_checklists_bom",
        ])
        bv = run_databricks(f"""
            SELECT {cols}
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
    """True quando NÃO há instalação agendada E NENHUM dispositivo instalado."""
    sup = dados["supply"]
    bv = dados["bv"]
    agendado = (sup.get("agendado") or 0) == 1
    instalado = (sup.get("instalado_flag") or 0) == 1
    completeness = _to_float(bv.get("INSTALL_COMPLETENESS")) or 0
    tem_dispositivo = instalado or completeness > 0
    return not agendado and not tem_dispositivo


def classificar_nota(score):
    s = _to_float(score)
    if s is None:
        return "Sem dados", "#9AA0A6"
    if s >= 3:
        return "Saudável", "#1DB954"
    if s >= 2:
        return "Atenção", "#F5A623"
    return "Crítico", "#E5484D"


# ----------------------------------------------------------------------------
# Mensagens de WhatsApp
# ----------------------------------------------------------------------------
def msg_boas_vindas(nome_cliente: str, empresa: str, analista: str) -> str:
    saud = f"Olá, {nome_cliente}! Tudo bem?" if nome_cliente else "Olá! Tudo bem?"
    alvo = f"a {empresa}" if empresa else "a sua operação"
    return (
        f"{saud}\n\n"
        f"Sou o {analista}, analista de treinamento da Cobli, e vou acompanhar {alvo} "
        f"de perto pelos próximos 3 meses. Meu papel é ajudar sua equipe a configurar a "
        f"plataforma, tirar dúvidas e garantir que vocês comecem a extrair valor da "
        f"operação o quanto antes.\n\n"
        f"Pode contar comigo por aqui sempre que precisar. Vou estar do seu lado em cada "
        f"etapa dessa fase inicial. 😊"
    )


def msg_kickoff_instalacao(empresa: str) -> str:
    alvo = f"da {empresa}" if empresa else "da sua frota"
    return (
        f"Para darmos início à instalação dos equipamentos {alvo}, preciso de algumas "
        f"informações para agendar tudo com o time técnico.\n\n"
        f"Quando puder, me envie por aqui, por favor:\n"
        f"• 2 opções de data e horário\n"
        f"• Endereço completo da instalação\n"
        f"• Nome e telefone do responsável no local\n"
        f"• Placas dos veículos\n\n"
        f"Fico à disposição para acompanhar isso de perto e garantir que a instalação "
        f"aconteça da melhor forma possível."
    )


def msg_acesso(link: str) -> str:
    return (
        f"Para você já começar a acessar o painel da Cobli, é só concluir seu cadastro "
        f"por este link:\n{link}\n\n"
        f"Qualquer dúvida no caminho, me chama por aqui."
    )


# ----------------------------------------------------------------------------
# UI helpers
# ----------------------------------------------------------------------------
def bloco_mensagem(titulo: str, texto: str, key: str):
    st.markdown(f"**{titulo}**")
    st.text_area("msg", texto, height=len(texto) // 2 + 90, key=key, label_visibility="collapsed")


def linha_criterio(label, ok, valor, unidade):
    icon = "✅" if ok else "❌" if ok is not None else "⚪"
    v = _to_float(valor)
    if v is None:
        vtxt = "sem dado"
    elif unidade == "%":
        vtxt = f"{v:.0f}%"
    else:
        vtxt = f"{v:.0f}"
    st.markdown(f"{icon}  {label}  ·  **{vtxt}**")


# ----------------------------------------------------------------------------
# App
# ----------------------------------------------------------------------------
st.title("🚚 Kickoff de Onboarding · Cobli")
st.caption("Analise o Basic Value da frota e monte o kickoff de WhatsApp a partir do Deal ID do HubSpot.")

with st.sidebar:
    st.header("Dados do kickoff")
    deal_id = st.text_input("Deal ID (HubSpot)", placeholder="ex.: 47596831034")
    email_cliente = st.text_input("E-mail do cliente (link de acesso)", placeholder="cliente@empresa.com")
    nome_cliente = st.text_input("Nome do contato", placeholder="ex.: João")
    analista = st.text_input("Seu nome (analista)", value=ANALISTA_PADRAO)
    buscar = st.button("Analisar frota", type="primary", use_container_width=True)

if not buscar:
    st.info("Preencha o Deal ID na barra lateral e clique em **Analisar frota**.")
    st.stop()

if not deal_id.strip():
    st.error("Informe o Deal ID.")
    st.stop()

try:
    dados = fetch_deal(deal_id)
except Exception as e:
    st.error(f"Erro ao consultar o Databricks: {e}")
    st.stop()

bv = dados["bv"]
sup = dados["supply"]
empresa = (bv.get("company_name") or sup.get("company_name") or "").strip()
nome_final = nome_cliente.strip() or (sup.get("cliente_nome") or "").strip()

if not dados["company_id"]:
    st.warning("Não encontrei empresa associada a este Deal ID no Databricks. Confira o número.")

# ---- Cabeçalho da frota ----
st.subheader(empresa or f"Deal {dados['deal_id']}")
c1, c2, c3, c4 = st.columns(4)
score = bv.get("basic_value_score")
label, cor = classificar_nota(score)
c1.metric("Basic Value (0–4)", f"{_to_float(score):.2f}" if _to_float(score) is not None else "—")
c1.markdown(f"<span style='color:{cor};font-weight:600'>{label}</span>", unsafe_allow_html=True)
c2.metric("Instalação", f"{_to_float(bv.get('INSTALL_COMPLETENESS')) or 0:.0f}%")
c3.metric("CSM", bv.get("csm") or "—")
c4.metric("Status", bv.get("status_da_empresa") or "—")

st.divider()

# ---- Pilares ----
if bv:
    st.markdown("### Basic Value por pilar")
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
                ok = bv.get(bom_col)
                ok = None if ok is None else bool(ok)
                linha_criterio(label_c, ok, bv.get(raw_col), un)
else:
    st.info("Sem registro de Basic Value na semana atual para esta empresa.")

st.divider()

# ---- Situação da instalação ----
nao_iniciada = instalacao_nao_iniciada(dados)
st.markdown("### Situação da instalação")
if nao_iniciada:
    st.error("Instalação **não iniciada**: sem agendamento e sem dispositivos instalados. Enviar kickoff de instalação.")
else:
    partes = []
    if (sup.get("agendado") or 0) == 1:
        dm = sup.get("data_marcada")
        partes.append(f"agendada{f' para {pd.to_datetime(dm).date()}' if dm else ''}")
    if (sup.get("instalado_flag") or 0) == 1 or (_to_float(bv.get("INSTALL_COMPLETENESS")) or 0) > 0:
        partes.append("com dispositivos instalados")
    st.success("Instalação " + " e ".join(partes) + "." if partes else "Instalação em andamento.")

st.divider()

# ---- Mensagens de WhatsApp ----
st.markdown("### Mensagens de WhatsApp")
st.caption("Revise, ajuste se precisar e copie para o cliente.")

bloco_mensagem("1. Boas-vindas", msg_boas_vindas(nome_final, empresa, analista.strip() or ANALISTA_PADRAO), "m_bv")

# Acesso ao painel
st.markdown("**2. Acesso ao painel**")
if email_cliente.strip():
    try:
        link, row = get_invite_link(email_cliente.strip())
        if link:
            st.text_area("msg_acesso", msg_acesso(link), height=140, key="m_ac", label_visibility="collapsed")
            st.caption(f"Convite encontrado · fleet_id: {row['fleet_id']} · criado em {row['creation_date']}")
        else:
            st.warning("E-mail não encontrado na base de convites (severino). Confira o e-mail ou gere um novo convite no cadastro.")
    except Exception as e:
        st.error(f"Erro ao consultar convites: {e}")
else:
    st.caption("Informe o e-mail do cliente na barra lateral para gerar o link de acesso.")

# Kickoff de instalação (condicional)
if nao_iniciada:
    bloco_mensagem("3. Kickoff de instalação (agendamento)", msg_kickoff_instalacao(empresa), "m_ki")
else:
    st.markdown("**3. Kickoff de instalação**")
    st.caption("Não necessário: instalação já agendada ou iniciada.")
