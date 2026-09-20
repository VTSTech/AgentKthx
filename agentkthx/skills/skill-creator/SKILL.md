---
name: skill-creator
description: Create, edit, or improve AgentSkills for AgentNova. Use when creating a new skill from scratch or when asked to improve, review, or clean up an existing skill. Triggers on phrases like "create a skill", "make a skill", "improve this skill", "review the skill".
license: MIT
---

# Skill Creator

Guidance for creating effective AgentSkills for AgentNova.

## What Are Skills?

Skills are modular packages that extend an agent's capabilities by providing specialized knowledge, workflows, and tools. Think of them as "onboarding guides" for specific domains or tasks.

### Skill Structure

```
skill-name/
├── SKILL.md (required)
│   ├── YAML frontmatter (required)
│   │   ├── name: skill-name
│   │   └── description: When and how to use this skill
│   └── Markdown instructions
└── Bundled Resources (optional)
    ├── scripts/    - Executable code (Python/Bash)
    ├── references/ - Documentation loaded as needed
    └── assets/     - Templates, images, files for output
```

## Core Principles

### Be Concise

The context window is limited. Only add information the model doesn't already know. Prefer concise examples over verbose explanations.

### Match Freedom to Task

- **High freedom** (text instructions): Multiple approaches valid
- **Medium freedom** (pseudocode): Preferred pattern exists
- **Low freedom** (specific scripts): Exact sequence required

## Progressive Disclosure

Skills use a three-level loading system:

1. **Metadata** (~100 tokens): `name` + `description` - always visible
2. **Instructions** (<500 lines): SKILL.md body - loaded when skill triggers
3. **Resources** (as needed): scripts, references, assets - loaded on demand

Keep SKILL.md under 500 lines. Split content into references/ when approaching this limit.

## Creating a Skill

### Step 1: Define the Skill

Understand concrete usage examples:

- What functionality should it support?
- What would trigger this skill?
- What reusable resources would help?

### Step 2: Initialize

Use the init script to create the skill structure:

```bash
python scripts/init_skill.py <skill-name> --path <output-dir> [--resources scripts,references,assets]
```

### Step 3: Write SKILL.md

**Frontmatter:**

```yaml
---
name: my-skill
description: What the skill does and when to use it. Include trigger phrases.
---
```

**Body:** Write instructions for using the skill and its resources.

### Step 4: Add Resources (Optional)

- **scripts/**: Tested, working code for repetitive tasks
- **references/**: Documentation, schemas, examples
- **assets/**: Templates, images, boilerplate

### Step 5: Validate & Package

```bash
python scripts/validate.py <skill-folder>  # Check structure
python scripts/package_skill.py <skill-folder>  # Create .skill file
```

## Skill Naming

- Lowercase letters, digits, hyphens only
- Under 64 characters
- Verb-led phrases preferred (e.g., `pdf-rotate`, `code-review`)
- Folder name matches skill name

## Avoid

Do NOT create these files - they add clutter:

- README.md
- INSTALLATION_GUIDE.md
- CHANGELOG.md
- QUICK_REFERENCE.md

A skill should only contain what an AI agent needs to do the job.
