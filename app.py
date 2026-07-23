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

# IA (opcional)
MODELO_IA_PADRAO = "gpt-4o-mini"
TONS_IA = ["Próximo e caloroso", "Objetivo e direto", "Formal e institucional"]

SYSTEM_TOM_COBLI = (
    "Você é redator de Customer Success da Cobli, empresa brasileira de telemetria e gestão de frotas. "
    "Escreve mensagens de WhatsApp que um analista de onboarding envia ao cliente.\n\n"
    "TOM: especialista acessível, parceiro da rotina, objetivo e humano. Próximo sem perder profissionalismo. "
    "Sem marketing vazio, sem dramatização, sem culpar o cliente.\n\n"
    "REGRAS INEGOCIÁVEIS:\n"
    "- NUNCA invente dados, números, datas, nomes ou links. Use somente os fatos fornecidos. "
    "Se um dado não estiver nos fatos, não cite.\n"
    "- Mantenha exatamente os links e números que aparecem no rascunho.\n"
    "- Você pode incluir links do manual da Cobli (manual.cobli.co) SOMENTE se eles vierem nos fatos. "
    "Nunca invente ou adivinhe URLs do manual.\n"
    "- PROIBIDO usar travessão (—). Use ponto final ou vírgula.\n"
    "- PROIBIDO a expressão 'tempo real'.\n"
    "- Evite palavras em inglês quando houver equivalente em português (painel, não dashboard).\n"
    "- No máximo 3 emojis na mensagem inteira, sem exageros.\n"
    "- Formato WhatsApp: use *negrito* com asteriscos, quebras de linha e bullets com •.\n"
    "- Não use estruturas de texto de IA como 'não é X, é Y' nem tripletes de impacto.\n"
    "Responda apenas com o texto final da mensagem, pronto para enviar."
)

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

# Artigos reais do manual da Cobli (manual.cobli.co) por critério.
# Usados como referência para a IA (ela só pode citar links que vierem daqui).
MANUAL_LINKS = {
    "setup_grups_bom": "https://manual.cobli.co/docs/painel/grupos-de-veiculos",
    "setup_drivers_bom": "https://manual.cobli.co/docs/painel/motoristas",
    "setup_driver_identification_bom": "https://manual.cobli.co/docs/painel/associacao-de-trechos/associar-motorista-a-trechos",
    "basic_config_speed_limit_bom": "https://manual.cobli.co/docs/painel/veiculos/limite-de-velocidade",
    "basic_config_fleet_policy_bom": "https://manual.cobli.co/docs/painel/alertas/criar-regra",
    "basic_config_geofences_bom": "https://manual.cobli.co/docs/painel/geofences",
    "basic_config_checklists_bom": "https://manual.cobli.co/docs/painel/checklists/criar-checklist",
    "instalation_completeness_grade_bom": "https://manual.cobli.co/docs/painel/comece-aqui/checklist-de-ativacao",
}

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


_EMP_SIGLAS = {"LTDA", "ME", "EPP", "EIRELI", "MEI", "SA", "CIA", "S.A.", "S/A", "LTDA."}
_EMP_CONECTORES = {"de", "da", "do", "das", "dos", "e", "em"}


def format_empresa(nome):
    """Formata o nome da empresa quando vem todo em maiúsculas, mantendo siglas."""
    nome = (nome or "").strip()
    if not nome:
        return ""
    letras = [c for c in nome if c.isalpha()]
    # só ajusta se estiver majoritariamente em maiúsculas
    if letras and sum(c.isupper() for c in letras) / len(letras) < 0.7:
        return nome
    out = []
    for w in nome.split():
        base = w.upper().strip(".")
        if w.upper() in _EMP_SIGLAS or base in _EMP_SIGLAS:
            out.append(w.upper())
        elif w.lower() in _EMP_CONECTORES:
            out.append(w.lower())
        else:
            out.append(w.capitalize())
    s = " ".join(out)
    return s[:1].upper() + s[1:] if s else s


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
def msg_kickoff(nome, empresa, analista, link=None, incluir_instalacao=False, mencionar_videos=False):
    saud = f"Oi, {nome}! Tudo bem?" if nome else "Olá! Tudo bem?"
    alvo = f"a {empresa}" if empresa else "a sua operação"
    de_emp = f"da {empresa}" if empresa else "da sua frota"
    abertura = (
        f"{saud}\n\n"
        f"Meu nome é {analista} e eu sou o analista de onboarding da Cobli que vai acompanhar {alvo} "
        f"nos próximos 3 meses. Na prática, sou eu quem vai estar do seu lado nessa fase inicial: "
        f"organizar a instalação, configurar a plataforma junto com a sua equipe e destravar qualquer "
        f"ponto no caminho para vocês sentirem o valor da Cobli o quanto antes."
    )

    blocos = []
    if incluir_instalacao:
        blocos.append(
            f"Nosso primeiro passo é instalar os equipamentos nos veículos {de_emp}, que é o que liga a "
            f"telemetria e faz tudo começar a funcionar. Para eu já adiantar o agendamento com o time "
            f"técnico, me envie por aqui, por favor:\n"
            f"• 2 opções de data e horário\n"
            f"• Endereço completo da instalação\n"
            f"• Nome e telefone do responsável no local\n"
            f"• Placas dos veículos"
        )
    if link:
        if incluir_instalacao:
            blocos.append(f"Aproveitando, já liberei o seu acesso ao painel. É só concluir o cadastro por aqui "
                          f"e ir se ambientando: {link}")
        else:
            blocos.append(f"Para você já começar a acessar o painel, é só concluir o seu cadastro por aqui: {link}")
    if mencionar_videos:
        blocos.append(
            "Vou te enviar também dois vídeos rápidos de primeiros passos:\n"
            "• Como criar os usuários da sua equipe\n"
            "• Como cadastrar os motoristas\n\n"
            "Assim que o acesso estiver ativo, é só seguir o passo a passo."
        )

    fecho = ("Fico à disposição para acompanhar tudo de perto e garantir que essa fase inicial aconteça da "
             "melhor forma. Pode me chamar por aqui sempre que precisar, combinado? 😊")
    partes = [abertura] + blocos + [fecho]
    return "\n\n".join(partes)


def _saud(nome):
    return f"Oi, {nome}! Tudo bem?" if nome else "Oi! Tudo bem?"


def _pergunta_final(nome):
    return f"Me diz o melhor horário, {nome}?" if nome else "Me diz o melhor horário?"


def msg_gaps(nome, empresa, gaps, score, base_url):
    saud = _saud(nome)
    conta = f"da {empresa}" if empresa else "de vocês"
    frota = f"a {empresa}" if empresa else "a sua frota"
    if not gaps:
        s = _to_float(score)
        nota = (f" e o Basic Value em {s:.1f}".replace(".", ",")) if s is not None else ""
        return (f"{saud}\n\nPassando para comemorar: {frota} já está com a configuração completa{nota}. "
                f"É exatamente o patamar que a gente busca no onboarding. Sigo por aqui para o que precisar. 🎉")
    itens = "\n".join(linha_gap(t, c, base_url) for c, t in gaps)
    return (f"{saud}\n\nDei uma olhada na conta {conta} e separei o que ainda falta para a frota atingir "
            f"a configuração ideal da Cobli. Deixei o vídeo do passo a passo em cada item:\n\n{itens}\n\n"
            f"Consigo te acompanhar em cada um, com calma. Quer que a gente resolva os primeiros ainda essa "
            f"semana? {_pergunta_final(nome)}")


def msg_fase(i, nome, empresa, bv, base_url):
    saud = _saud(nome)
    a_emp = f"a {empresa}" if empresa else "a sua frota"
    de_emp = f"da {empresa}" if empresa else "da sua frota"
    intro = [
        f"Boas-vindas à Cobli! Nas próximas semanas vou acompanhar {a_emp} de perto para deixar tudo rodando "
        f"redondo. O primeiro passo é a instalação dos equipamentos e o seu acesso ao painel.",
        f"Com os equipamentos {de_emp} instalados, bora deixar a plataforma com a cara da sua operação. "
        f"Nesta etapa a gente organiza usuários, grupos e motoristas.",
        f"Agora vem a parte que mais gera resultado no dia a dia {de_emp}: limite de velocidade, políticas de "
        f"frota, locais de interesse e checklists.",
        f"Estamos fechando os primeiros 90 dias {de_emp} na Cobli. Bora revisar juntos os últimos ajustes para "
        f"garantir a configuração completa.",
    ][i]

    if i == 3:
        s = _to_float(bv.get("basic_value_score"))
        pend = gaps_abertos(bv)
        if s is not None and s >= META_BASIC_VALUE:
            nfmt = f"{s:.1f}".replace(".", ",")
            parabens = f"Parabéns pelo trabalho, {nome}!" if nome else "Parabéns pelo trabalho!"
            return (f"{saud}\n\n{intro}\n\nBoa notícia: {a_emp} já está em {nfmt} de Basic Value, acima da meta "
                    f"de 3. {parabens} Sigo à disposição para o que vier. 🎉")
        alvo = (f"em {s:.1f} de Basic Value".replace(".", ",")) if s is not None else "quase lá"
        itens = "\n".join(linha_gap(t, c, base_url) for c, t in pend)
        return (f"{saud}\n\n{intro}\n\n{a_emp[0].upper() + a_emp[1:]} está {alvo} e a meta é chegar a 3. "
                f"Faltam poucos ajustes:\n\n{itens}\n\nBora fechar esses itens juntos antes de encerrar o onboarding?")

    pend = pendentes_da_fase(i, bv)
    if not pend:
        return f"{saud}\n\n{intro}\n\nPor aqui está tudo certo nesta fase. Seguimos para a próxima etapa. 😊"
    itens = "\n".join(linha_gap(t, c, base_url) for c, t in pend)
    return (f"{saud}\n\n{intro}\n\nPara avançar nesta fase, faltam estes passos (deixei o vídeo de cada um):\n\n"
            f"{itens}\n\nConsigo te guiar item a item. Qual o melhor horário para a gente ver isso, {nome}?"
            if nome else
            f"{saud}\n\n{intro}\n\nPara avançar nesta fase, faltam estes passos (deixei o vídeo de cada um):\n\n"
            f"{itens}\n\nConsigo te guiar item a item. Qual o melhor horário para a gente ver isso?")


# ----------------------------------------------------------------------------
# IA (ChatGPT) - opcional
# ----------------------------------------------------------------------------
@st.cache_resource
def _openai_client():
    try:
        key = None
        if "openai" in st.secrets:
            key = dict(st.secrets["openai"]).get("api_key")
        key = key or st.secrets.get("OPENAI_API_KEY")
        if not key:
            return None
        from openai import OpenAI
        return OpenAI(api_key=key)
    except Exception:
        return None


def ia_disponivel():
    return _openai_client() is not None


def fatos_cliente(empresa, nome, bv, dias, idx, gaps, usuario_ativo, instal_nao_iniciada):
    """Dicionário de fatos REAIS para a IA usar (e não inventar)."""
    f = {"Empresa": empresa or "(desconhecida)", "Contato": nome or "(desconhecido)"}
    s = _to_float(bv.get("basic_value_score"))
    if s is not None:
        f["Basic Value atual (0 a 4)"] = f"{s:.2f}".replace(".", ",")
    f["Meta de Basic Value"] = "3"
    if dias is not None:
        f["Dia da jornada (de 90)"] = dias
    f["Fase atual"] = FASES[idx]["nome"]
    f["Tem usuário com acesso ao painel"] = "sim" if usuario_ativo else "não"
    f["Instalação já iniciada"] = "não" if instal_nao_iniciada else "sim"
    f["Pendências (o que falta)"] = "; ".join(t for _, t in gaps) if gaps else "nenhuma"
    links = [f"{t}: {MANUAL_LINKS[c]}" for c, t in gaps if c in MANUAL_LINKS]
    if links:
        f["Artigos do manual (use SOMENTE estes links, nunca invente outros)"] = " | ".join(links)
    return f


def gerar_mensagem_ia(model, fatos, base, objetivo, tom, curto=True):
    client = _openai_client()
    if client is None:
        return None
    fatos_txt = "\n".join(f"- {k}: {v}" for k, v in fatos.items())
    regra_curto = (
        "\n\nIMPORTANTE: escreva uma mensagem CURTA e escaneável para WhatsApp: vá direto ao ponto, "
        "poucas linhas, sem parágrafos longos. Mantenha os links e os itens da lista."
    ) if curto else ""
    user = (
        f"Objetivo da mensagem: {objetivo}\n"
        f"Tom desejado: {tom}\n\n"
        f"FATOS REAIS DO CLIENTE (vindos do Databricks; use SOMENTE estes, não invente "
        f"nenhum número, nome, data ou link):\n{fatos_txt}\n\n"
        f"RASCUNHO BASE (já traz a intenção e os pedidos corretos: dados de instalação, "
        f"link de acesso, menção aos vídeos, itens pendentes). Mantenha a mesma intenção e "
        f"todos os fatos, números e links exatamente como estão):\n{base}"
        f"{regra_curto}\n\n"
        f"Tarefa: reescreva a mensagem final no tom da Cobli, interpretando de forma breve o "
        f"momento do cliente a partir dos fatos (ex.: o que já avançou e o que priorizar agora), "
        f"sem repetir todos os números crus e sem inventar nada. Não deixe de fazer os pedidos e "
        f"não remova links. Responda apenas com o texto final para WhatsApp."
    )
    resp = client.chat.completions.create(
        model=model, temperature=0.5,
        messages=[{"role": "system", "content": SYSTEM_TOM_COBLI},
                  {"role": "user", "content": user}],
    )
    return resp.choices[0].message.content.strip()


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


def bloco_ia(base_msg, fatos, objetivo, telefone, key, model, tom):
    """Botão que reescreve a mensagem com IA (tom Cobli, sem inventar dados)."""
    if not ia_disponivel():
        return
    ss_key = f"ia_{key}"
    if st.button("✨ Personalizar com IA", key=f"btn_{key}"):
        with st.spinner("Gerando com IA no tom da Cobli..."):
            try:
                st.session_state[ss_key] = gerar_mensagem_ia(model, fatos, base_msg, objetivo, tom)
            except Exception as e:
                st.session_state[ss_key] = None
                st.error(f"Falha ao gerar com IA: {e}")
    saida = st.session_state.get(ss_key)
    if saida:
        st.markdown("**Versão personalizada pela IA** (revise antes de enviar)")
        st.text_area("ia_out", saida, height=max(260, len(saida) // 2), key=f"ta_{key}", label_visibility="collapsed")
        botao_whatsapp(saida, telefone, f"waia_{key}")


def bloco_ia_auto(base_msg, fatos, objetivo, telefone, key, model, tom):
    """Gera a mensagem por IA automaticamente (modo IA), com cache por cliente."""
    ss_key = f"iaauto_{key}_{model}"
    regen = st.button("🔄 Regenerar com IA", key=f"re_{key}")
    if regen or ss_key not in st.session_state:
        with st.spinner("Gerando com IA no tom da Cobli..."):
            try:
                st.session_state[ss_key] = gerar_mensagem_ia(model, fatos, base_msg, objetivo, tom, curto=True)
            except Exception as e:
                st.session_state[ss_key] = f"[Falha ao gerar com IA: {e}]\n\n{base_msg}"
    saida = st.session_state.get(ss_key) or base_msg
    st.caption(f"Gerado por IA ({model}) no tom da Cobli. Revise antes de enviar.")
    st.text_area(f"iaauto_{key}", saida, height=max(260, len(saida) // 2),
                 key=f"taauto_{key}", label_visibility="collapsed")
    botao_whatsapp(saida, telefone, f"waauto_{key}")


def render_indicacao(base_msg, objetivo, key, fatos, telefone, modo_ia, model, tom):
    """Mostra a indicação no modo escolhido: IA (auto) ou Padrão (texto pronto + botão IA opcional)."""
    if modo_ia and ia_disponivel():
        bloco_ia_auto(base_msg, fatos, objetivo, telefone, key, model, tom)
    else:
        if modo_ia and not ia_disponivel():
            st.info("Modo IA selecionado, mas a chave da OpenAI não está no secrets. Mostrando a versão padrão.")
        st.text_area(f"ind_{key}", base_msg, height=max(300, len(base_msg) // 2),
                     key=f"ta_{key}", label_visibility="collapsed")
        botao_whatsapp(base_msg, telefone, f"wa_{key}")
        bloco_ia(base_msg, fatos, objetivo, telefone, f"m_{key}", model, tom)


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
    st.divider()
    st.subheader("Indicação")
    modo = st.radio(
        "Modo", ["Padrão", "IA (gpt-4o-mini)"],
        help="Padrão usa os textos prontos. IA gera tudo com o ChatGPT, no tom da Cobli, já analisado.",
    )
    modo_ia = modo.startswith("IA")
    modelo_ia = MODELO_IA_PADRAO
    if ia_disponivel():
        tom_ia = st.selectbox("Tom da mensagem", TONS_IA, index=0)
    else:
        tom_ia = TONS_IA[0]
        st.caption("Chave da OpenAI ausente em [openai] no secrets. O modo IA cai para o padrão até você configurá-la.")
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
empresa = format_empresa(bv.get("company_name") or sup.get("company_name") or "")
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
_nao_iniciada = instalacao_nao_iniciada(dados)
fatos = fatos_cliente(empresa, nome_final, bv, dias, idx, gaps, usuario_ativo, _nao_iniciada)

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
        sem_acesso = not usuario_ativo
        if sem_acesso:
            st.warning("Este cliente ainda **não tem usuário com acesso** ao painel. Cole o link de convite para incluí-lo na mensagem.")
            link = (st.text_input("Link de acesso do cliente", placeholder="https://cadastro.cobli.co/invites/XXXXXXXX").strip() or None)
        else:
            st.success("Cliente já tem usuário com acesso ao painel. O link de convite não é necessário.")
        msg = msg_kickoff(nome_final, empresa, analista_final, link=link,
                          incluir_instalacao=_nao_iniciada, mencionar_videos=sem_acesso)
        render_indicacao(msg, "Mensagem de kickoff/boas-vindas do onboarding",
                         f"{dados['hs_id']}_kick", fatos, telefone_final, modo_ia, modelo_ia, tom_ia)
        if sem_acesso:
            st.divider()
            st.caption("Como o cliente ainda não tem acesso, envie também os tutoriais de primeiros passos:")
            mostrar_videos(["setup_user_bom", "setup_drivers_bom"], base_url, prefix="kickoff")
    else:
        st.markdown("### Próximos passos (features que faltam)")
        if not bv:
            st.info("Sem Basic Value para gerar a lista de pendências.")
        else:
            msg = msg_gaps(nome_final, empresa, gaps, score, base_url)
            render_indicacao(msg, "Mensagem sobre as features que faltam para o cliente",
                             f"{dados['hs_id']}_gaps", fatos, telefone_final, modo_ia, modelo_ia, tom_ia)
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
            render_indicacao(texto, f"Comunicação da fase {i + 1} ({FASES[i]['nome']}) da jornada de onboarding",
                             f"{dados['hs_id']}_fase{i}", fatos, telefone_final, modo_ia, modelo_ia, tom_ia)
            pend_cols = [c for c, _ in (gaps_abertos(bv) if i == 3 else pendentes_da_fase(i, bv))]
            mostrar_videos(pend_cols, base_url, prefix=f"fase{i}")
