"""Catalog factories receive the owned database session explicitly."""


def tool_catalog(request, session, owner):
    return request.app.state.integrations.tool_catalog(session)


def mcp_catalog(request, session, owner):
    return request.app.state.integrations.mcp_catalog(session)


def skill_catalog(request, session, owner):
    return request.app.state.integrations.skill_catalog(session)


def external_catalog(request, session, owner):
    return request.app.state.integrations.external_catalog(session)
