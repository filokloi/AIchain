# Production Checklist (AIchain)

## Repository Governance
- [ ] `dev` branch exists and is used for feature work
- [ ] `main` protected: PR required, at least 1 review, status checks required
- [ ] direct pushes to `main` disabled

## CI/CD
- [ ] `Validate` workflow passes on PR and push
- [ ] `AIchain — Intelligence Arbitration Cycle` workflow succeeds
- [ ] GitHub Pages deployment succeeds
- [ ] Live URL works: `https://filokloi.github.io/AIchain/`
- [ ] Routing table URL works: `https://filokloi.github.io/AIchain/ai_routing_table.json`

## Security & Hygiene
- [ ] no secrets in repository history/content
- [ ] logs/runtime artifacts ignored by `.gitignore`
- [ ] keys sourced from environment/secret store only

## Skill Consistency
- [ ] `ai-chain-skill/SKILL.md` matches actual file structure
- [ ] install script default routing URL points to live Pages JSON
- [ ] `bridge_config.json` default URL points to live Pages JSON

## Runtime Validation
- [ ] initial sync succeeds (`--sync`)
- [ ] watcher starts (`--watch`)
- [ ] escalation/revert path tested
- [ ] specialist trigger pin tested

## Release Gate
- [ ] all checks green
- [ ] no critical TODOs
- [ ] tagged release created (optional)
