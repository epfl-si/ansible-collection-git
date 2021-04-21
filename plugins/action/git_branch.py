#!/usr/bin/env python3

"""
Enforce postconditions on a Git branch in a checked out repository.
"""

from ansible.plugins.action import ActionBase
from ansible_collections.epfl_si.actions.plugins.module_utils.subactions import Subaction

class GitBranchAction(ActionBase):
    def run(*args, **kwargs):
        return {}

ActionModule = GitBranchAction
