# Ansible Collection - epfl_si.git

This Ansible collection provides a number of utilities for dealing
with Git repositories, checkouts and branches.

Git version 2.22 or higher is required.

## Synopsis

```yaml

- name: Verify that this branch is checked out; otherwise, bail out
  epfl_si.git.git_branch:
    repository: '{{ image_serving_build_dir }}'
    branch: master
    verify:
      checked_out: true

- name: Ensure that this branch is checked out (check it out if not)
  epfl_si.git.git_branch:
    repository: '{{ image_serving_build_dir }}'
    branch: master
    ensure:
      checked_out: true

- name: Verify that the checked out revision is a descendant from some branch
  epfl_si.git.git_branch:
    repository: '{{ image_serving_build_dir }}'
    branch: master
    verify:
      pull:
        from: 'refs/remotes/origin/prod'

- name: Same, but attempt to fix it if it isn't; then push
  epfl_si.git.git_branch:
    repository: '{{ image_serving_build_dir }}'
    branch: master
    ensure:
      checked_out: true
      pull:
        from: 'refs/remotes/origin/master'
        rebase: true
        autostash: true
      push:
        force_with_lease: true

- name: Commit to the current branch (whichever it is) and force-push it to origin/prod
  epfl_si.git.git_branch:
    path: '{{ image_serving_build_dir }}'
    ensure:
      committed:
        message: |
          [auto] Committed by Ansible
      push:
        to: 'refs/remotes/origin/prod'
        force: yes
```
