---
name: product-experience-design
description: When creating or redesigning critical pages, entry points, navigation, page layouts or multistep interactions, derive information architecture, flows, wireframes, states and acceptance criteria from the user's task.
---

# Product experience design

[Chinese edition](SKILL.md)

1. Start with the task the user wants to complete. Record the primary user, trigger, entry point, successful outcome and known obstacles. Consult the existing product, requirements and feedback first. Mark unverified judgments as assumptions and explain what would verify them.
2. Organize information in the order users look for things and make decisions. Name navigation and content groups in familiar terms. Draw the main path and necessary branches. For each step, give its entry point, primary action, result and next step. Consider deep links, back navigation, cancellation and re-entry after an interruption. For an existing product, inspect adjacent pages and reuse entry points that already support the task.
3. Draw a low-fidelity wireframe or provide an equivalent structural description for critical pages. Identify what users see first, the information needed for a decision, the primary button and secondary actions. Specify the reading and interaction order on narrow screens, required reflow and items to preserve. When the structure changes, explain how it helps users find an entry point, compare options or finish the task.
4. Walk through the main success path and the exceptions users will encounter. Check empty content, filters with no results, loading, failures, permission restrictions and retries as applicable. Describe what the user sees, how to recover, and which filters, inputs or progress must persist when returning. Check keyboard order, accessible names and critical actions on narrow screens.
5. Hand off user tasks, flows, critical page structures, required states and assumptions to validate. Give QA observable acceptance scenarios with an entry point, action and result. A small change may need just one page-structure table and a few scenarios. For changes across pages, record design decisions about entry points and states and hand them to implementation under the existing Pioneers approval rules.

## Example: Find shoes by size

From the category entry point on the home page, open Shoes. The list shows the category title, filters and result count, then product cards. On desktop, filters can sit beside the list. On a narrow screen, put the Filter action and selected criteria below the list title. Product cards show the name, price and availability first.

QA checks three scenarios. Selecting a size updates the selected tag and result count and displays only matching products. When there are no results, the user can clear the filter and restore the list. Opening a product detail and returning preserves the selected criteria and previous scroll position.

## Sources

- Information architecture, task flows, states and engineering handoff. [magnus919 · Product Design and UX, MIT](https://github.com/magnus919/agent-skills/blob/main/product-design-and-ux/SKILL.md)
- Wireframes and layout descriptions for critical screens. [Emmraan · UI/UX, MIT](https://github.com/Emmraan/agent-skills/blob/main/skills/design-ux/ui-ux/SKILL.md)
- Interaction choices, exceptions and recovery. [mblode · Product Design, MIT](https://github.com/mblode/agent-skills/blob/main/skills/product-design/SKILL.md)
