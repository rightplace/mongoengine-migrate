from mongoengine_migrate.exceptions import MigrationError
from mongoengine_migrate.utils import Slotinit

from typing import Dict, List


class Migration(Slotinit):
    # TODO: make it dict-like, not list-like
    """Object represents one migration

    Contains information which is set in migration:
    * name -- migration file name without '.py' suffix
    * dependencies -- name list of migrations which this migration is
      dependent by
    * applied -- is migration was applied or not. Taken from database
    """
    __slots__ = ('name', 'dependencies', 'applied', 'module')
    defaults = {'applied': False}

    def get_forward_actions(self):
        # FIXME: type checking, attribute checking
        # FIXME: tests
        return self.module.forward

    def get_backward_actions(self):
        # FIXME: type checking, attribute checking
        # FIXME: tests
        return reversed(self.module.forward)


class MigrationsGraph:
    def __init__(self):
        # Following two variables contains the same migrations digraph
        # but from different points of view
        self._parents: Dict[str, List[Migration]] = {}  # {child_name: [parent_obj...]}
        self._children: Dict[str, List[Migration]] = {}  # {parent_name: [child_obj...]}

        self._migrations: Dict[str, Migration] = {}  # {migration_name: migration_obj}

    @property
    def initial(self):
        """Return initial migration object"""
        for name, parents in self._parents.items():
            if not parents:
                return self._migrations[name]

    @property
    def last(self):
        """Return last children migration object"""
        for name, children in self._children.items():
            if not children:
                return self._migrations[name]

    @property
    def migrations(self):
        """Migration objects dict"""
        return self._migrations

    def add(self, migration: Migration):
        """
        Add migration to the graph. If object with that name exists
        in graph then it will be replaced.
        :param migration: Migration object
        :return:
        """
        self._parents[migration.name] = []
        self._children[migration.name] = []

        for partner in self._migrations.values():
            if partner.name == migration.name:
                continue
            if partner.name in migration.dependencies:
                self._parents[migration.name].append(partner)
                self._children[partner.name].append(migration)
            if migration.name in partner.dependencies:
                self._children[migration.name].append(partner)
                self._parents[partner.name].append(migration)

        self._migrations[migration.name] = migration

    def clear(self):  # TODO: tests
        """
        Clear graph
        :return:
        """
        self._parents = {}
        self._children = {}
        self._migrations = {}

    def verify(self):
        """
        Verify migrations graph to be satisfied to consistency rules
        Graph must not have loops, disconnections.
        Also it should have single initial migration and (for a while)
        single last migration.
        :raises MigrationError: if problem in graph was found
        :return:
        """
        # FIXME: This function is not used anywhere
        initials = []
        last_children = []

        for name, obj in self._migrations.items():
            if not self._parents[name]:
                initials.append(name)
            if not self._children[name]:
                last_children.append(name)
            if len(obj.dependencies) > len(self._parents[name]):
                diff = set(obj.dependencies) - {x.name for x in self._parents[name]}
                raise MigrationError(f'Unknown dependencies in migration {name!r}: {diff}')
            if name in (x.name for x in self._children[name]):
                raise MigrationError(f'Found migration which dependent on itself: {name!r}')

        if len(initials) == len(last_children) and len(initials) > 1:
            raise MigrationError(f'Migrations graph is disconnected, history segments '
                                 f'started on: {initials!r}, ended on: {last_children!r}')
        if len(initials) > 1:
            raise MigrationError(f'Several initial migrations found: {initials!r}')

        if len(last_children) > 1:
            raise MigrationError(f'Several last migrations found: {last_children!r}')

        if not initials or not last_children:
            raise MigrationError(f'No initial or last children found')

    def walk_down(self, from_node: Migration, unapplied_only=True, _node_counters=None):
        """
        Walks down over migrations graph. Iterates in order as migrations
        should be applied.

        We're used modified DFS (depth-first search) algorithm to traverse
        the graph. Migrations are built into directed graph (digraph)
        counted from one root node to the last ones. Commonly DFS tries to
        walk to the maximum depth firstly.

        But there are some problems:
        * Graph can have directed cycles. This happens when some
          migration has several dependencies. Therefore we'll walk
          over such migration several times
        * Another problem arises from the first one. Typically we must walk
          over all dependencies before a dependent migration will be
          touched. DFS will process only one dependency before get to
          a dependent migration

        In order to manage it we use counter for each migration
        (node in digraph) initially equal to its parents count.
        Every time the algorithm gets to node it decrements this counter.
        If counter > 0 after that then don't touch this node and
        break traversing on this depth and go up. If counter == 0 then
        continue traversing.
        :param from_node: current node in graph
        :param unapplied_only: if True then return only unapplied migrations
         or return all migrations otherwise
        :param _node_counters:
        :raises MigrationError: if graph has a closed cycle
        :return: Migration objects generator
        """
        # FIXME: may yield nodes not related to target migration if branchy graph
        # FIXME: if migration was applied after its dependencies unapplied then it is an error
        # FIXME: should have stable migrations order
        if _node_counters is None:
            _node_counters = {}
        if from_node is None:
            return ()
        _node_counters.setdefault(from_node.name, len(self._parents[from_node.name]) or 1)
        _node_counters[from_node.name] -= 1

        if _node_counters[from_node.name] > 0:
            # Stop on this depth if not all parents has been viewed
            return

        if _node_counters[from_node.name] < 0:
            # A node was already returned and we're reached it again
            # This means there is a closed cycle
            raise MigrationError(f'Found closed cycle in migration graph, '
                                 f'{from_node.name!r} is repeated twice')

        if not (from_node.applied and unapplied_only):
            yield from_node

        for child in self._children[from_node.name]:
            yield from self.walk_down(child, unapplied_only, _node_counters)

    def walk_up(self, from_node: Migration, applied_only=True, _node_counters=None):
        """
        Walks up over migrations graph. Iterates in order as migrations
        should be reverted.

        We're using modified DFS (depth-first search) algorithm which in
        reversed order (see `walk_down`). Instead of looking at node
        parents count we're consider children count in order to return
        all dependent nodes before dependency.

        Because of the migrations graph may have many orphan child nodes
        they all should be passed as parameter
        :param from_node:  last children node we are starting for
        :param applied_only: if True then return only applied migrations,
         return all migrations otherwise
        :param _node_counters:
        :raises MigrationError: if graph has a closed cycle
        :return: Migration objects generator
        """
        # FIXME: may yield nodes not related to reverting if branchy graph
        # FIXME: if migration was unapplied before its dependencies applied then it is an error
        if _node_counters is None:
            _node_counters = {}
        if from_node is None:
            return ()
        _node_counters.setdefault(from_node.name, len(self._children[from_node.name]) or 1)
        _node_counters[from_node.name] -= 1

        if _node_counters[from_node.name] > 0:
            # Stop in this depth if not all children has been viewed
            return

        if _node_counters[from_node.name] < 0:
            # A node was already returned and we're reached it again
            # This means there is a closed cycle
            raise MigrationError(f'Found closed cycle in migration graph, '
                                 f'{from_node.name!r} is repeated twice')

        if from_node.applied or not applied_only:
            yield from_node

        for child in self._parents[from_node.name]:
            yield from self.walk_up(child, applied_only, _node_counters)

    def __iter__(self):
        return iter(self.walk_down(self.initial, unapplied_only=False))

    def __reversed__(self):
        return iter(self.walk_up(self.last, applied_only=False))

    def __contains__(self, migration: Migration):
        return migration in self._migrations.values()

    def __eq__(self, other):
        if other is self:
            return True

        return all(migr == other._migrations.get(name) for name, migr in self._migrations.items())

    def __ne__(self, other):
        return not self.__eq__(other)