# .dsh/skills · 技能库清单

> 源：`wshobson/agents @ 0874a27b`（MIT，见 `vendor.lock.json`）
> 同步脚本：`bash scripts/sync-skills.sh`（幂等，读 `vendor/wshobson-agents`，denylist 见下）
> 命名：`<plugin>--<skill>`；每家 skill 含 `SKILL.md` 与可选 `references/`。

## 已纳入（按插件分组）

| 插件 | 数量 | skills |
|---|---:|---|
| agent-teams | 6 | multi-reviewer-patterns, parallel-debugging, parallel-feature-development, task-coordination-strategies, team-communication-protocols, team-composition-patterns |
| api-scaffolding | 1 | fastapi-templates |
| avoid-ai-writing | 1 | avoid-ai-writing |
| backend-development | 9 | api-design-principles, architecture-patterns, cqrs-implementation, event-store-design, microservices-patterns, projection-patterns, saga-orchestration, temporal-python-testing, workflow-orchestration-patterns |
| before-you-build | 1 | before-you-build |
| block-no-verify | 1 | block-no-verify-hook |
| cicd-automation | 4 | deployment-pipeline-design, github-actions-templates, gitlab-ci-patterns, secrets-management |
| cloud-infrastructure | 8 | cost-optimization, hybrid-cloud-networking, istio-traffic-management, linkerd-patterns, mtls-configuration, multi-cloud-architecture, service-mesh-observability, terraform-module-library |
| conductor | 3 | context-driven-development, track-management, workflow-patterns |
| data-engineering | 4 | airflow-dag-patterns, data-quality-frameworks, dbt-transformation-patterns, spark-optimization |
| database-design | 1 | postgresql-table-design |
| developer-essentials | 11 | auth-implementation-patterns, bazel-build-optimization, code-review-excellence, debugging-strategies, e2e-testing-patterns, error-handling-patterns, git-advanced-workflows, monorepo-management, nx-workspace-patterns, sql-optimization-patterns, turborepo-caching |
| documentation-generation | 3 | architecture-decision-records, changelog-automation, openapi-spec-generation |
| documentation-standards | 2 | grounded-vault, hads |
| file-conversion | 1 | file-conversion |
| framework-migration | 4 | angular-migration, database-migration, dependency-upgrade, react-modernization |
| frontend-mobile-development | 4 | nextjs-app-router-patterns, react-native-architecture, react-state-management, tailwind-design-system |
| incident-response | 4 | connectivity-triage, incident-runbook-templates, on-call-handoff-patterns, postmortem-writing |
| javascript-typescript | 4 | javascript-testing-patterns, modern-javascript-patterns, nodejs-backend-patterns, typescript-advanced-types |
| kubernetes-operations | 4 | gitops-workflow, helm-chart-scaffolding, k8s-manifest-generator, k8s-security-policies |
| llm-application-dev | 8 | embedding-strategies, hybrid-search-implementation, langchain-architecture, llm-evaluation, prompt-engineering-patterns, rag-implementation, similarity-search-patterns, vector-index-tuning |
| llm-finetuning | 10 | checkpoint-promotion, dataset-curation, eval-harness-first, finetuning-method-selection, grpo-rlvr-training, lora-qlora-recipes, preference-optimization, quantized-export, trace-to-training-data, vision-sft |
| observability-monitoring | 4 | distributed-tracing, grafana-dashboards, prometheus-configuration, slo-implementation |
| plugin-eval | 1 | evaluation-methodology |
| protect-mcp | 1 | protect-mcp-setup |
| python-development | 16 | async-python-patterns, python-anti-patterns, python-background-jobs, python-code-style, python-configuration, python-design-patterns, python-error-handling, python-observability, python-packaging, python-performance-optimization, python-project-structure, python-resilience, python-resource-management, python-testing-patterns, python-type-safety, uv-package-manager |
| reverse-engineering | 4 | anti-reversing-techniques, binary-analysis-patterns, memory-forensics, protocol-reverse-engineering |
| review-agent-governance | 1 | review-agent-setup |
| security-scanning | 5 | attack-tree-construction, sast-configuration, security-requirement-extraction, stride-analysis-patterns, threat-mitigation-mapping |
| shell-scripting | 3 | bash-defensive-patterns, bats-testing-patterns, shellcheck-configuration |
| ship-mate | 1 | scan |
| signed-audit-trails | 1 | signed-audit-trails-recipe |
| skill-forge-essentials | 3 | ai-debt-detector, session-guard, visual-edit-precision |
| superself | 1 | superself |
| systems-programming | 3 | go-concurrency-patterns, memory-safety-patterns, rust-async-patterns |

**合计纳入：138 个 skill / 35 个插件。**

## 排除（离域，未同步）

| 插件 | 原因 |
|---|---|
| accessibility-compliance | 无障碍合规，离域 |
| blockchain-web3 | 区块链/Web3，离域 |
| brand-landingpage | 品牌落地页，离域 |
| business-analytics | 商业分析，离域 |
| content-marketing | 内容营销，离域 |
| customer-sales-automation | 销售自动化，离域 |
| dgx-spark-ops | DGX 运维，离域 |
| dotnet-contribution | .NET 贡献流程，离域 |
| game-development | 游戏开发，离域 |
| hermes-tweet | 推特发帖，离域 |
| hr-legal-compliance | HR/法务，离域 |
| machine-learning-ops | MLOps，离域（微调类 skill 另计） |
| meigen-ai-design | AI 设计，离域 |
| payment-processing | 支付处理，离域 |
| pptx-deck-creation | 演示文稿生成，与安全产品无关 |
| quantitative-trading | 量化交易，离域 |
| seo-analysis-monitoring | SEO，离域 |
| seo-content-creation | SEO，离域 |
| seo-technical-optimization | SEO，离域 |
| social-publishing | 社媒发布，离域 |
| startup-business-analyst | 创业分析，离域 |
| ui-design | 纯 UI 视觉设计，本产品为 CLI/内核，非必要 |

> 需要时可删 denylist 重跑 `sync-skills.sh` 全量纳入（用户已授权“全部全局化也好”）。

## 与本项目的关系

- 开发/规划防错：`code-review-excellence` `debugging-strategies` `parallel-debugging` `ai-debt-detector` `session-guard` `task-coordination-strategies`
- 安全工程：`security-scanning--*` `reverse-engineering--*` `protect-mcp-setup` `review-agent-setup` `signed-audit-trails-recipe`
- 工程质量：`python-development--*` `developer-essentials--*` `github-actions-templates` `secrets-management` `architecture-decision-records`
- 观测/评测：`observability-monitoring--*` `llm-evaluation` `prompt-engineering-patterns`
