from __future__ import annotations

from sakura_context import PromptBlock, PromptRecipe


def render_blocks(blocks: list[PromptBlock] | tuple[PromptBlock, ...]) -> str:
    """按稳定换行规则渲染提示词块。"""

    rendered: list[str] = []
    for block in blocks:
        body = block.body.strip()
        if not body:
            continue
        if block.title:
            rendered.append(f"【{block.title}】\n{body}")
        else:
            rendered.append(body)
    return "\n\n".join(rendered).strip()


def render_recipe(recipe: PromptRecipe) -> str:
    """渲染完整提示词配方。"""

    return render_blocks(tuple(recipe.blocks))
