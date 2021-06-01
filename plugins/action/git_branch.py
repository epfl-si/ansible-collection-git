#!/usr/bin/env python3

"""
Enforce postconditions on a Git branch in a checked out repository.
"""

import re
import json

from ansible.plugins.action import ActionBase
from ansible_collections.epfl_si.actions.plugins.module_utils.subactions import Subaction
from ansible_collections.epfl_si.actions.plugins.module_utils.ansible_api import AnsibleActions, AnsibleResults
from ansible_collections.epfl_si.actions.plugins.module_utils.postconditions import Postcondition, run_postcondition, DeclinedToEnforce
from ansible.errors import AnsibleError, AnsibleActionFail


class ActionModule (ActionBase):
    @AnsibleActions.run_method
    def run (self, task_args, ansible_api):
        if "verify" in task_args:
            if "ensure" in task_args:
                raise AnsibleError("`verify` and `ensure` are mutually exclusive")
            verify = True
            todo = task_args.pop("verify")
        elif "ensure" in task_args:
            verify = False
            todo = task_args.pop("ensure")
        else:
            raise AnsibleError("One of `verify` and `ensure` must be present")

        result = {}
        try:
            for postcondition in as_postconditions(
                    task_args, todo,
                    ansible_api=ansible_api, verify=verify, result=result):
                AnsibleResults.update(result, run_postcondition(postcondition, ansible_api.check_mode))
                # Note: all the sub-tasks ran by the postcondition's GitSubaction
                # (e.g. their stdout, and more) also update `result`.s
            return result
        except AnsibleActionFail as e:
            if result["failed"]:
                return result
            else:
                # Defensive - Shouldn't happen
                raise e


def as_postconditions (task_args, todo, **kwargs):
    """Decode the YAML task args structure into a list of Postcondition objects.

    :param dict task_args: The YAML data structure provided by Ansible,
                           sans its `verify` or `ensure` key (which must be passed
                           as `todo` instead)
    :param dict todo:      The value for either the `verify` or the `ensure` key
                           from the Ansible-provided task args
    :param dict kwargs:    Additional kwargs to pass to every Postcondition
                           constructor
    :raises TypeError: if either `todo` or `task_args` contains weird stuff
    """

    branch          = task_args.pop("branch", None)
    repository_path = task_args.pop("repository", None)
    git_command     = task_args.pop("git_command", None)

    def new_postcondition (clazz, **more_kwargs):
        more_kwargs.update(kwargs)
        more_kwargs["branch_name"]     = branch
        more_kwargs["repository_path"] = repository_path
        more_kwargs["git_command"]     = git_command
        return clazz(**more_kwargs)

    postconditions = []
    def push_postcondition (*args, **kwargs):
        postconditions.append(new_postcondition(*args, **kwargs))

    if todo.pop("checked_out", None):
        push_postcondition(GitBranchCheckedOut)

    committed = todo.pop("committed", None)
    if committed is None:
        pass
    elif committed is True:
        push_postcondition(GitBranchCommitted)
    elif isinstance(committed, dict):
        push_postcondition(GitBranchCommitted, message=committed["message"])
    else:
        raise TypeError("Unsupported type for `committed`: %s" % type(committed).__name__)

    pull_list = todo.pop("pull", [])
    if not isinstance(pull_list, list):
        pull_list = [pull_list]
    for pull in pull_list:
        pull_params = {}
        if isinstance(pull, dict):
            pull_params = dict(
                # You would think one could just write **pull here;
                # but no, Python is decidedly the language of people
                # who cannot parse, and cannot be bothered to learn.
                pull_from=pull.get("from"),
                rebase=pull.get("rebase"),
                autostash=pull.get("autostash"))
        elif pull is True:
            pass
        else:
            raise TypeError("Unsupported type found in `pull`: %s" % type(pull).__name__)
        push_postcondition(GitBranchPulled, **pull_params)

    push = todo.pop("push", None)
    if push:
        push_params = {}
        if isinstance(push, dict):
            push_params.update(push)
        elif push is True:
            pass
        else:
            raise TypeError("Unsupported type for `push`: %s" % type(push).__name__)
        push_postcondition(GitBranchPushed, **push_params)

    if todo:
        raise TypeError("git_branch: unknown parameter(s) under `verify` or `ensure`: %s" %
                        json.dumps(todo))
    if task_args:
        raise TypeError("git_branch: unknown task parameter(s): %s" %
                        json.dumps(task_args))

    return postconditions


class GitBranchPostconditionBase (Postcondition):
    """A common base class for all Postcondition's in this module."""
    def __init__ (self, repository_path, branch_name, ansible_api, verify, result, git_command):
        """Must be called before running the postcondition."""
        self.repository_path = repository_path
        self.branch_name = branch_name
        self.ansible_api = ansible_api
        self.verify = verify
        self.result = result
        self.git_command = git_command

    def passive (self):
        if self.verify:
            return "%s: does not hold, bailing out (`verify` only)" % self.explainer()

    @property
    def moniker (self):
        "branch `%s`" % self.branch_name if self.branch_name else "current branch"

    @property
    def git (self):
        if not hasattr(self, "__git"):
            self.__git = GitSubaction(self.ansible_api, self.repository_path, self.result,
                                      git_command=self.git_command)
        return self.__git


class GitBranchCheckedOut (GitBranchPostconditionBase):
    def explainer (self):
        return ("checked_out: %s is checked out" % self.moniker if self.branch_name
            else "checked_out: any branch is checked out")

    def holds (self):
        # I do suggest you install git 2.22 or higher. This is 2021 (at least).
        actual_branch_name = self.git.query("branch", "--show-current")["stdout"].strip()
        if self.branch_name:
            return actual_branch_name == self.branch_name
        else:
            return actual_branch_name != ""

    def enforce (self):
        if not self.branch_name:
            raise AnsibleActionFailed("HEAD is detached, cannot fix (no branch name specified)")
        # TODO: should support checkout -B as well, with additional flags.
        self.git.change("checkout", self.branch_name)


class GitBranchCommitted (GitBranchPostconditionBase):
    def __init__ (self, message=None, **kwargs):
        super(GitBranchCommitted, self).__init__(**kwargs)
        self.message = message

    def explainer (self):
        return "repository is committed to " + self.moniker

    def holds (self):
        git_status_red   = self.git.query("diff", "--exit-code",
                                          expected_rc=(0, 1))
        git_status_green = self.git.query("diff", "--cached", "--exit-code",
                                          expected_rc=(0, 1))
        return (git_status_red["rc"] == 0 and
                git_status_green["rc"] == 0)

    def passive (self):
        if self.message is not None:
            return None
        else:
            return "Cannot auto-commit — No `.commit.message` specified in task"

    def enforce (self):
        self.git.change("commit", "-m", self.message)

class GitBranchPushOrPullBase (GitBranchPostconditionBase):
    def __init__(self, branch_spec, **kwargs):
        super(GitBranchPushOrPullBase, self).__init__(**kwargs)
        self.branch_spec = branch_spec

    @property
    def remote (self):
        remote, _unused_remote_branch = self._get_upstream_or_throw()
        return remote

    @property
    def remote_branch (self):
        _unused_remote, remote_branch = self._get_upstream_or_throw()
        return remote_branch

    @property
    def remote_branch_qualified (self):
        return "%s/%s" % (self.remote, self.remote_branch)

    def _get_upstream_or_throw (self):
        if not hasattr(self, "__upstream"):
            self.__upstream = (
                self.git.query_upstream(self.branch_spec)
                if self.branch_spec is not None
                else self.git.query_upstream())
        if self.__upstream[0] is None:
            raise AnsibleError("No upstream branch configured for %s" % (
                self.branch_spec if self.branch_spec is not None
                else "the current branch"))
        return self.__upstream


class GitBranchPulled (GitBranchPushOrPullBase):
    def __init__ (self, pull_from, rebase=False, autostash=False, **kwargs):
        super(GitBranchPulled, self).__init__(branch_spec=pull_from, **kwargs)
        self.rebase    = rebase
        self.autostash = autostash

    def explainer (self):
        return "%s is up-to-date (pulled) from %s" % (self.moniker, self.remote_branch_qualified)

    def holds (self):
        return not (self._needs_fetch() or self._needs_pull())

    def _needs_fetch (self):
        return ".." in self.git.query("fetch", "--dry-run", self.remote, self.remote_branch)["stderr"]

    def _needs_pull (self):
        # Unfortunately `git pull --dry-run` does *not* correctly signal a branch that
        # has been fetched but not pulled yet (tested with git 2.31.1)
        return self.git.query("merge-base", "--is-ancestor",
                              self.remote_branch_qualified, "HEAD",
                              expected_rc=(0, 1))["rc"] == 1

    def enforce (self):
        pull_flags = []
        if self.rebase:
            pull_flags.append("--rebase")
        if self.autostash:
            pull_flags.append("--autostash")
        pull_flags.extend([self.remote, self.remote_branch])
        self.git.change("pull", *pull_flags)


class GitBranchPushed (GitBranchPushOrPullBase):
    def __init__ (self, to=None, force=False, force_with_lease=False, **kwargs):
        super(GitBranchPushed, self).__init__(branch_spec=to, **kwargs)
        self.force = force
        self.force_with_lease = force_with_lease

    def explainer (self):
        return "%s is pushed" % self.moniker

    def holds (self):
        return not self._needs_push()

    def _needs_push (self):
        return "->" in self.git.query("push", "--dry-run", *self._push_args)["stderr"]

    def enforce (self):
        self.git.change("push", *self._push_args)

    @property
    def _push_args (self):
        return (
            ["--force"] if self.force
            else ["--force-with-lease"] if self.force_with_lease
            else []) + [self.remote, self.remote_branch]


class GitSubaction (Subaction):
    def __init__ (self, ansible_api, repository_path, result, git_command=None):
        super(GitSubaction, self).__init__(ansible_api)
        self.__repository_path = repository_path
        self.__git_command = git_command if git_command is not None else "git"
        self.result = result

    def get_current_branch (self):
        return self._git_query(["branch", "--show-current"])

    def query (self, *argv, expected_rc=None, **kwargs):
        if expected_rc is not None:
            if "failed_when" in kwargs:
                raise TypeError("expected_rc and failed_when are mutually exclusive")
            else:
                def failed_when(result):
                    return result["rc"] not in expected_rc
                kwargs["failed_when"] = failed_when
        return super(GitSubaction, self).query("command", self._to_command_dict(argv), **kwargs)

    def change (self, *argv, **kwargs):
        return super(GitSubaction, self).change("command", self._to_command_dict(argv), **kwargs)

    def _to_command_dict (self, argv):
        if len(argv) == 1 and " " in argv[0]:
            shell_command = re.sub(r"(^|\$\(|`)git\b", r"\1" + self.__git_command,
                                   "git " + argv[0])

            return dict(_uses_shell=True,
                        chdir=self.__repository_path,
                        _raw_params = shell_command)
        else:
            return dict(_uses_shell=False,
                        chdir=self.__repository_path,
                        argv=[self.__git_command] + list(argv))

    def query_upstream (self, branch=None):
        """Returns the upstream branch.

        :param branch: The name of the local branch, or None to indicate the
                       current branch

        :return: A (remote_name, remote_branch_name) tuple, or (None, None) if
                 no upstream branch is configured for this branch.
        """
        find_out_upstream_cmd = 'for-each-ref --format="%%(upstream:short)" "%s"' % (
                    '$(git symbolic-ref -q "HEAD")' if branch is None
                    else "refs/heads/%s" % branch)
        branch_spec = self.query(find_out_upstream_cmd)["stdout"].strip()
        if branch_spec:
            return self._deconstruct_remote_name(branch_spec)
        else:
            return (None, None)

    def _deconstruct_remote_name (self, branch_spec):
        if "/" not in branch_spec:
            return ("origin", branch_spec)
        perhaps_remote, perhaps_branch = branch_spec.split("/", 1)
        if perhaps_remote in self._remotes:
            return (perhaps_remote, perhaps_branch)
        else:
            return ("origin", branch_spec)

    @property
    def _remotes (self):
        if not hasattr(self, "__remotes"):
            self.__remotes = self.query("remote")["stdout"].splitlines()
        return self.__remotes
