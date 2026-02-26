# Production Checklist (AIchain)

## Repository Governance
- [x] `dev` branch exists and is used for feature work
- [x] `main` protected: PR required, at least 1 review, status checks required
- [x] direct pushes to `main` disabled

## CI/CD
- [x] `Validate` workflow passes on PR and push
- [x] `AIchain — Intelligence Arbitration Cycle` workflow succeeds
- [x] GitHub Pages deployment succeeds
- [x] Live URL works: `https://filokloi.github.io/AIchain/`
- [x] Routing table URL works: `https://filokloi.github.io/AIchain/ai_routing_table.json`

## Security & Hygiene
- [x] no secrets in repository history/content
- [x] logs/runtime artifacts ignored by `.gitignore`
- [x] keys sourced from environment/secret store only

## Skill Consistency
- [x] `ai-chain-skill/SKILL.md` matches actual file structure
- [x] install script default routing URL points to live Pages JSON
- [x] `bridge_config.json` default URL points to live Pages JSON

## Runtime Validation
- [x] initial sync succeeds (`--sync`)
- [x] watcher starts (`--watch`)
- [x] escalation/revert path tested
- [x] specialist trigger pin tested

## Release Gate
- [x] all checks green
- [x] no critical TODOs
- [ ] tagged release created (optional)
