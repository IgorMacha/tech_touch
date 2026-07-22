# Kickoff de Onboarding · Cobli

App em Streamlit para o onboarding tech touch. A partir de um **Deal ID do HubSpot**, o app:

1. Descobre a empresa/frota (via `gold.cubo_supply.supply_cube`, com fallback em `gold.cubo_contratos.fct_contract_products`).
2. Lê o **Basic Value** da semana atual em `gold.customer_success_reports.basic_value`, quebrando nos 3 pilares (Instalação, Setup, Configuração Básica) e seus critérios.
3. Verifica a **situação da instalação** (agendada / dispositivos instalados).
4. Gera o **link de acesso ao painel** a partir do e-mail do cliente (tabela `invites` do severino).
5. Monta as **mensagens de WhatsApp** prontas para copiar: boas-vindas (analista de treinamento pelos próximos 3 meses), acesso ao painel e, quando a instalação não começou, o pedido de agendamento.

## Como rodar

```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # e preencha os valores
streamlit run app.py
```

## Lógica do Basic Value

`basic_value_score` é a média das 3 notas de pilar (cada uma de 0 a 4). Exemplo real: Instalação 4, Setup 3, Config 3 → 3,33. Classificação usada no app: **Saudável ≥ 3**, **Atenção 2–3**, **Crítico < 2**.

Critérios por pilar (colunas `*_bom` já calculadas na tabela):

- **Instalação:** >90% das instalações concluídas.
- **Setup:** ≥2 perfis, ≥2 grupos, ≥2 motoristas, ≥60% das viagens com motorista identificado.
- **Configuração Básica:** limite de velocidade em ≥60% dos veículos, ≥2 regras de política, ≥2 geofences, ≥2 checklists.

## Regra da mensagem de instalação

A mensagem de agendamento aparece quando a instalação **não começou**: sem data agendada em `supply_cube` **e** sem nenhum dispositivo instalado (`instalacao__data_realizada` / `entry_date_instalado` nulos e `INSTALL_COMPLETENESS = 0`).

## ⚠️ Segurança das credenciais

As credenciais do severino e do Databricks ficam em `.streamlit/secrets.toml`, **fora do código**. A senha do severino que circulou em texto é de produção: o recomendado é rotacioná-la e usar um usuário somente-leitura dedicado a esta aplicação. Não suba `secrets.toml` para repositório público.
