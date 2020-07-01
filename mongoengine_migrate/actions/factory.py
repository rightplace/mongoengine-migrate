from typing import Iterable, Type

from dictdiffer import patch

import mongoengine_migrate.flags as flags
from mongoengine_migrate.exceptions import ActionError
from mongoengine_migrate.schema import Schema
from .base import (
    actions_registry,
    BaseFieldAction,
    BaseDocumentAction,
    BaseAction
)


def build_actions_chain(left_schema: Schema, right_schema: Schema) -> Iterable[BaseAction]:
    """
    Build full Action objects chain which suitable for such schema
    change.
    :param left_schema: current schema
    :param right_schema: schema collected from mongoengine models
    :return: iterable of Action objects
    """
    action_chain = []

    # Actions registry sorted by priority
    registry = list(sorted(actions_registry.values(), key=lambda x: x.priority))

    left_schema = left_schema.copy()
    document_types = get_all_document_types(left_schema, right_schema)
    for action_cls in registry:
        if issubclass(action_cls, BaseDocumentAction):
            new_actions = list(
                build_document_action_chain(action_cls, left_schema, right_schema, document_types)
            )
        elif issubclass(action_cls, BaseFieldAction):
            new_actions = list(
                build_field_action_chain(action_cls, left_schema, right_schema, document_types)
            )
        else:
            continue

        for action in new_actions:
            left_schema = patch(action.to_schema_patch(left_schema), left_schema)
        action_chain.extend(new_actions)
        document_types = get_all_document_types(left_schema, right_schema)

    if right_schema != left_schema:
        from dictdiffer import diff
        print(list(diff(left_schema, right_schema)))
        # TODO: ability to force process without error
        raise ActionError('Could not reach current schema after applying whole Action chain. '
                          'This could be a problem in some Action which does not react to schema'
                          ' change it should react or produces wrong schema diff')

    return action_chain


def build_document_action_chain(action_cls: Type[BaseDocumentAction],
                                left_schema: Schema,
                                right_schema: Schema,
                                document_types: Iterable[str]) -> Iterable[BaseAction]:
    """
    Walk through schema changes, and produce chain of Action objects
    of given type which could handle schema changes from left to right
    :param action_cls: Action type to consider
    :param left_schema:
    :param right_schema:
    :param document_types: list of document types to inspect
    :return: iterable of suitable Action objects
    """
    for document_type in document_types:
        action_obj = action_cls.build_object(document_type, left_schema, right_schema)
        if action_obj is not None:
            # TODO: handle patch errors (if schema is corrupted)
            left_schema = patch(action_obj.to_schema_patch(left_schema), left_schema)
            yield action_obj


def build_field_action_chain(action_cls: Type[BaseFieldAction],
                             left_schema: Schema,
                             right_schema: Schema,
                             document_types: Iterable[str]) -> Iterable[BaseAction]:
    """
    Walk through schema changes, and produce chain of Action objects
    of given type which could handle schema changes from left to right
    :param action_cls: Action type to consider
    :param left_schema:
    :param right_schema:
    :param document_types: list of document types to inspect
    :return: iterable of suitable Action objects
    """
    for document_type in document_types:
        # Take all fields to detect if they created, changed or dropped
        all_fields = left_schema.get(document_type, {}).keys() | \
                     right_schema.get(document_type, {}).keys()
        for field in all_fields:
            action_obj = action_cls.build_object(document_type,
                                                 field,
                                                 left_schema,
                                                 right_schema)
            if action_obj is not None:
                # TODO: handle patch errors (if schema is corrupted)
                left_schema = patch(action_obj.to_schema_patch(left_schema), left_schema)
                yield action_obj


def get_all_document_types(left_schema: Schema, right_schema: Schema) -> Iterable[str]:
    """
    Return list of all document types collected from both schemas
    :param left_schema:
    :param right_schema:
    :return:
    """
    all_document_types = left_schema.keys() | right_schema.keys()

    # Handle embedded documents before collections
    all_document_types = [c for c in all_document_types
                          if c.startswith(flags.EMBEDDED_DOCUMENT_NAME_PREFIX)] + \
                         [c for c in all_document_types
                          if not c.startswith(flags.EMBEDDED_DOCUMENT_NAME_PREFIX)]

    return all_document_types
