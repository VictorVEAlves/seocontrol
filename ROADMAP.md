# SEO Control Center - Roadmap

Objetivo: evoluir o auditor atual para uma plataforma de controle total de SEO, com coleta recorrente, priorizacao, historico, backlog de acoes e publicacao assistida.

## Estado atual

- CLI principal em `run.py`.
- Auditorias de GSC, on-page, duplicidade, links quebrados, clusters internos, PageSpeed e GEO.
- Geracao de conteudo por IA com fila em `pending_changes.json`.
- Publicacao assistida na Bagy via Playwright.
- Relatorios HTML estaticos em `reports/`.
- Persistencia no Supabase com historico de runs, URLs, snapshots, issues e recommendations.
- Backlog priorizado com exportacao CSV/HTML.
- Dashboard Flask local em `app.py`.
- Monitor operacional com `python run.py --module monitor`.
- Diagnostico de ambiente com `python run.py --module doctor`.
- Fila de conteudo no Supabase com `python run.py --module content-sync`.
- Ideias de blog por query com `python run.py --module blog-ideas`.
- Enriquecimento por IA para ideias de blog com `python run.py --module blog-ideas --ai`.
- Analise executiva por IA com `python run.py --module ai-analysis`.
- Provider OpenRouter para testar modelos gratuitos/chineses como Qwen, GLM, DeepSeek e Kimi quando disponiveis.
- Dashboard operacional com rotas para issues, backlog, conteudo, runs e detalhe por URL.

## Riscos imediatos

- Segredos devem ficar somente no `.env`; nunca em `config.py` ou arquivos versionados.
- O dashboard ainda e inicial e precisa virar uma UI de operacao diaria.
- As integracoes ainda dependem de exports manuais do GSC.

## Fase de fundacao - concluida

1. Criar banco e historico no Supabase. [OK]
   - Tabelas: `sites`, `urls`, `crawl_runs`, `page_snapshots`, `gsc_queries`, `gsc_pages`, `issues`, `recommendations`, `content_changes`.
   - Guardar cada execucao com data, escopo, status e metricas.

2. Criar um motor de prioridades. [OK]
   - Score combinando impacto, confianca e esforco.
   - Exemplo: impressoes GSC, posicao, CTR gap, gravidade tecnica, pagina estrategica e dificuldade estimada.
   - Saida: backlog ordenado por dinheiro/oportunidade, nao por modulo.

3. Separar coleta, analise e apresentacao. [OK]
   - `collectors/`: GSC, crawler, PageSpeed, sitemap, Bagy.
   - `analyzers/`: on-page, links, duplicidade, schema, indexacao, conteudo.
   - `actions/`: geracao, revisao, publicacao, exportacao.
   - `dashboard/`: painel local.

4. Adicionar testes pequenos para funcoes criticas. [OK]
   - Normalizacao de URLs.
   - Leitura de CSVs do GSC.
   - Regras de score.
   - Deteccao de problemas on-page sem depender da internet.

## Modulos que faltam para "controle total"

- Sitemap e robots.txt: detectar paginas ausentes, bloqueadas, canonicals inconsistentes. [MVP OK]
- Indexabilidade: noindex, canonical cruzado, redirects, status, parametros. [MVP OK]
- SERP snippets: controle de title/description propostos versus publicados. [MVP OK]
- Conteudo: gap por categoria, entidades, perguntas frequentes, intent e canibalizacao por query. [MVP OK]
- Produtos/categorias: estoque, profundidade de categoria, breadcrumbs, filtros indexaveis. [MVP OK]
- Links internos: sugestoes automaticas de anchor e origem/destino. [MVP OK]
- Logs ou crawl recorrente: detectar regressao antes de virar perda de trafego. [MVP OK]
- Dashboard: saude por cluster, tarefas abertas, ganhos estimados, historico e alertas. [MVP parcial]
- Automacao recorrente: script `scripts/run_monitor.ps1` para agendar no Windows. [MVP OK]

## Proxima evolucao para producao

- Trocar exports manuais do GSC por API oficial com OAuth.
- Trocar heuristicas de produtos/categorias por seletores reais da Bagy.
- Adicionar autenticacao ao dashboard antes de expor fora da maquina local.
- Criar alertas por e-mail/Slack quando regressao critica aparecer.
- Adicionar historico visual por URL e por cluster no dashboard.

## Primeiro MVP recomendado

1. Persistir resultados no Supabase.
2. Gerar uma tabela unica de `issues`.
3. Criar `python run.py --module backlog` para listar as 20 acoes mais importantes.
4. Exportar backlog em HTML/CSV.
5. Evoluir o dashboard Flask para uma UI completa.

Status: o comando `--module backlog` ja existe em uma primeira versao. O schema Supabase, `--save-db`, exportacao de backlog e painel Flask tambem foram adicionados para salvar e visualizar runs, URLs, snapshots, issues e recommendations.

## Comandos atuais uteis

```bash
python run.py --all --gsc ./gsc_exports
python run.py --module gsc --gsc ./gsc_exports
python run.py --module onpage --urls /lacoste /tommy-hilfiger
python run.py --module broken-links --max-pages 200
python run.py --module pagespeed --urls /lacoste
python run.py --module backlog --gsc ./gsc_exports --top 20
python run.py --module backlog --gsc ./gsc_exports --top 20 --save-db --no-report
python run.py --module backlog --gsc ./gsc_exports --top 20 --export-backlog --no-report
python run.py --module blog-ideas --gsc ./gsc_exports --top 20 --save-db --no-report
python run.py --module blog-ideas --gsc ./gsc_exports --top 10 --ai --save-db --no-report
python run.py --module blog-ideas --gsc ./gsc_exports --top 10 --ai --provider openrouter --no-report
python run.py --module ai-analysis --gsc ./gsc_exports --urls /lacoste --no-report
python run.py --module generate --urls /lacoste
python run.py --module review
```

## Providers de IA

Configure no `.env`:

```bash
OPENROUTER_API_KEY=
OPENROUTER_MODEL=openai/gpt-oss-20b:free
OPENROUTER_FALLBACK_MODELS=deepseek/deepseek-v4-flash:free,qwen/qwen3-next-80b-a3b-instruct:free,z-ai/glm-4.5-air:free,meta-llama/llama-3.3-70b-instruct:free,openrouter/free
AI_PROVIDER_ORDER=openrouter,groq,gemini,mistral,anthropic
```

Modelos OpenRouter uteis para testar:

```bash
OPENROUTER_MODEL=openrouter/free
OPENROUTER_MODEL=qwen/qwen3-32b:free
OPENROUTER_MODEL=z-ai/glm-4.5-air:free
OPENROUTER_MODEL=deepseek/deepseek-chat:free
OPENROUTER_MODEL=moonshotai/kimi-k2:free
```

Como os modelos `:free` mudam conforme disponibilidade, o sistema agora tenta:

1. `OPENROUTER_MODEL`
2. cada item de `OPENROUTER_FALLBACK_MODELS`
3. outro provider configurado em `AI_PROVIDER_ORDER`

Use `openrouter/free` quando quiser deixar o roteador escolher automaticamente.
