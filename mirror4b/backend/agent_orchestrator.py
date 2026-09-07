from __future__ import annotations

from dataclasses import dataclass

from backend.schemas import (
    AgentAction,
    AgentActionType,
    AgentWorkflowState,
    IntentResult,
    IntentType,
)


APP_ACTION_INTENTS = {
    IntentType.SELECT_PERSON_IMAGE,
    IntentType.UPLOAD_PERSON_IMAGE,
    IntentType.START_TRY_ON,
    IntentType.CHANGE_BACKGROUND,
    IntentType.SAVE_RESULT,
    IntentType.CONTACT_SALES,
    IntentType.PURCHASE_PRODUCT,
    IntentType.OPEN_PRODUCT_DETAIL,
    IntentType.NAVIGATE_APP,
}


@dataclass(frozen=True)
class AgentPlan:
    handled: bool
    message: str | None
    action: AgentAction | None
    workflow_state: AgentWorkflowState


def merge_workflow_state(
    stored_state: AgentWorkflowState | None,
    request_state: AgentWorkflowState | None,
    selected_product_ids: list[str],
) -> AgentWorkflowState:
    state = stored_state or AgentWorkflowState()
    if request_state is not None:
        state = state.model_copy(
            update=request_state.model_dump(exclude_unset=True)
        )
    return state.model_copy(
        update={"selected_product_ids": list(dict.fromkeys(selected_product_ids))}
    )


def _product_required_plan(
    state: AgentWorkflowState,
    pending_action: AgentActionType,
) -> AgentPlan | None:
    if state.selected_product_ids:
        return None
    next_state = state.model_copy(
        update={
            "current_page": "product_library",
            "pending_workflow": pending_action,
        }
    )
    return AgentPlan(
        handled=True,
        message="请先从右侧商品列表选择一件衣服，我会接着完成刚才的操作。",
        action=AgentAction(
            type=AgentActionType.OPEN_PRODUCT_LIBRARY,
            label="选择服装",
            payload={"resume_action": pending_action.value},
        ),
        workflow_state=next_state,
    )


def _try_on_plan(
    state: AgentWorkflowState,
    pending_action: AgentActionType,
) -> AgentPlan:
    missing_product = _product_required_plan(state, pending_action)
    if missing_product is not None:
        return missing_product
    if state.person_image_url is None:
        next_state = state.model_copy(
            update={
                "current_page": "person_images",
                "pending_workflow": pending_action,
            }
        )
        return AgentPlan(
            handled=True,
            message="可以，先选择或上传一张清晰的人物照片，完成后我会继续刚才的试穿操作。",
            action=AgentAction(
                type=AgentActionType.UPLOAD_PERSON_IMAGE,
                label="上传人物照片",
                payload={"resume_action": pending_action.value},
            ),
            workflow_state=next_state,
        )
    next_state = state.model_copy(
        update={
            "current_page": "try_on",
            "pending_workflow": pending_action,
        }
    )
    return AgentPlan(
        handled=True,
        message="人物照片和服装都已选好，可以开始生成 AI 试穿效果。",
        action=AgentAction(
            type=AgentActionType.START_TRY_ON,
            label="开始 AI 试穿",
            payload={
                "person_image_url": state.person_image_url,
                "product_ids": state.selected_product_ids,
            },
        ),
        workflow_state=next_state,
    )


def _resume_completed_action(
    state: AgentWorkflowState,
) -> AgentPlan | None:
    completed = state.last_completed_action
    pending = state.pending_workflow
    if (
        completed
        in {
            AgentActionType.SELECT_PERSON_IMAGE,
            AgentActionType.UPLOAD_PERSON_IMAGE,
        }
        and state.person_image_url is not None
        and pending
        in {
            AgentActionType.START_TRY_ON,
            AgentActionType.CHANGE_BACKGROUND,
            AgentActionType.SAVE_RESULT,
        }
    ):
        return _try_on_plan(
            state.model_copy(update={"last_completed_action": None}),
            pending,
        )
    if completed == AgentActionType.START_TRY_ON and state.try_on_result_url is not None:
        completed_state = state.model_copy(
            update={
                "current_page": "try_on_result",
                "last_completed_action": None,
                "pending_workflow": None,
            }
        )
        if pending == AgentActionType.CHANGE_BACKGROUND:
            return AgentPlan(
                True,
                "试穿已经完成，可以继续选择或描述想要的背景。",
                AgentAction(
                    type=AgentActionType.CHANGE_BACKGROUND,
                    label="更换背景",
                    payload={"source_image_url": state.try_on_result_url},
                ),
                completed_state.model_copy(
                    update={"pending_workflow": AgentActionType.CHANGE_BACKGROUND}
                ),
            )
        if pending == AgentActionType.SAVE_RESULT:
            return AgentPlan(
                True,
                "试穿已经完成，可以保存当前效果。",
                AgentAction(
                    type=AgentActionType.SAVE_RESULT,
                    label="保存试穿效果",
                    payload={"result_url": state.try_on_result_url},
                ),
                completed_state.model_copy(
                    update={"pending_workflow": AgentActionType.SAVE_RESULT}
                ),
            )
        return AgentPlan(
            True,
            "AI 试穿已经完成。你可以继续更换背景、保存效果或查看商品详情。",
            None,
            completed_state,
        )
    if (
        completed == AgentActionType.CHANGE_BACKGROUND
        and state.background_image_url is not None
    ):
        return AgentPlan(
            True,
            "背景已经更换完成，可以保存最新效果或继续查看商品。",
            None,
            state.model_copy(
                update={
                    "current_page": "background",
                    "last_completed_action": None,
                    "pending_workflow": None,
                }
            ),
        )
    if completed == AgentActionType.SAVE_RESULT:
        return AgentPlan(
            True,
            "试穿效果已经保存完成，可以继续咨询导购或查看商品详情。",
            None,
            state.model_copy(
                update={
                    "current_page": "saved",
                    "last_completed_action": None,
                    "pending_workflow": None,
                }
            ),
        )
    return None


def plan_agent_action(
    *,
    intent_result: IntentResult,
    workflow_state: AgentWorkflowState,
    user_message: str,
) -> AgentPlan:
    intent = intent_result.intent
    state = workflow_state
    resumed_plan = _resume_completed_action(state)
    if resumed_plan is not None:
        return resumed_plan
    if intent not in APP_ACTION_INTENTS:
        return AgentPlan(False, None, None, state)

    if intent == IntentType.SELECT_PERSON_IMAGE:
        state = state.model_copy(
            update={"current_page": "person_images"}
        )
        return AgentPlan(
            True,
            "请选择一张清晰的正面全身或半身人物照片。",
            AgentAction(
                type=AgentActionType.SELECT_PERSON_IMAGE,
                label="选择人物照片",
            ),
            state,
        )

    if intent == IntentType.UPLOAD_PERSON_IMAGE:
        state = state.model_copy(
            update={"current_page": "person_images"}
        )
        return AgentPlan(
            True,
            "请上传一张清晰的正面全身或半身人物照片。",
            AgentAction(
                type=AgentActionType.UPLOAD_PERSON_IMAGE,
                label="上传人物照片",
            ),
            state,
        )

    if intent == IntentType.START_TRY_ON:
        return _try_on_plan(state, AgentActionType.START_TRY_ON)

    if intent == IntentType.CHANGE_BACKGROUND:
        if state.try_on_result_url is None:
            prerequisite = _try_on_plan(state, AgentActionType.CHANGE_BACKGROUND)
            return AgentPlan(
                True,
                "当前还没有试穿结果，请先完成 AI 试穿，再更换背景。",
                prerequisite.action,
                prerequisite.workflow_state,
            )
        state = state.model_copy(
            update={
                "current_page": "background",
                "pending_workflow": AgentActionType.CHANGE_BACKGROUND,
            }
        )
        return AgentPlan(
            True,
            "可以，请选择背景图片，或者描述想要的背景场景。",
            AgentAction(
                type=AgentActionType.CHANGE_BACKGROUND,
                label="更换背景",
                payload={
                    "source_image_url": state.try_on_result_url,
                    "prompt": user_message,
                },
            ),
            state,
        )

    if intent == IntentType.SAVE_RESULT:
        result_url = state.background_image_url or state.try_on_result_url
        if result_url is None:
            prerequisite = _try_on_plan(state, AgentActionType.SAVE_RESULT)
            return AgentPlan(
                True,
                "当前还没有可保存的试穿结果，请先完成 AI 试穿。",
                prerequisite.action,
                prerequisite.workflow_state,
            )
        state = state.model_copy(
            update={
                "current_page": "saved",
                "pending_workflow": AgentActionType.SAVE_RESULT,
            }
        )
        return AgentPlan(
            True,
            "已经找到当前最新的试穿效果，可以保存到手机。",
            AgentAction(
                type=AgentActionType.SAVE_RESULT,
                label="保存试穿效果",
                payload={"result_url": result_url},
            ),
            state,
        )

    if intent == IntentType.NAVIGATE_APP:
        route = "product_library"
        if "试穿" in user_message:
            route = "try_on"
        elif "人像" in user_message:
            route = "person_images"
        elif "收藏" in user_message:
            route = "saved"
        state = state.model_copy(update={"current_page": route})
        return AgentPlan(
            True,
            "好的，我已经为你准备好对应的功能入口。",
            AgentAction(
                type=AgentActionType.NAVIGATE_APP,
                label="打开功能页面",
                payload={"route_name": route},
            ),
            state,
        )

    required = _product_required_plan(
        state,
        {
            IntentType.OPEN_PRODUCT_DETAIL: AgentActionType.OPEN_PRODUCT_DETAIL,
            IntentType.CONTACT_SALES: AgentActionType.CONTACT_SALES,
            IntentType.PURCHASE_PRODUCT: AgentActionType.PURCHASE_PRODUCT,
        }[intent],
    )
    if required is not None:
        return required

    product_id = state.selected_product_ids[0]
    if intent == IntentType.OPEN_PRODUCT_DETAIL:
        action_type = AgentActionType.OPEN_PRODUCT_DETAIL
        label = "查看商品详情"
        message = "已定位到当前选中的商品，可以查看详细信息。"
        page = "product_detail"
    elif intent == IntentType.CONTACT_SALES:
        action_type = AgentActionType.CONTACT_SALES
        label = "咨询门店导购"
        message = "已带上当前商品信息，可以继续咨询门店导购。"
        page = "product_detail"
    else:
        action_type = AgentActionType.PURCHASE_PRODUCT
        label = "前往购买"
        message = "已定位到当前选中的商品，可以进入门店购买流程。"
        page = "product_detail"
    state = state.model_copy(
        update={"current_page": page, "pending_workflow": action_type}
    )
    return AgentPlan(
        True,
        message,
        AgentAction(
            type=action_type,
            label=label,
            payload={"product_id": product_id},
        ),
        state,
    )
