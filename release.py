#!/usr/bin/python3

#
# Step interactively through the release process for osbuild
#
# Requires: pip install ghapi (https://ghapi.fast.ai/)

import argparse
import subprocess
import sys
import os
import shutil
import getpass
from re import search
import requests
from datetime import date
from ghapi.all import GhApi


class fg:
    BOLD = '\033[1m'  # bold
    OK = '\033[32m'  # green
    INFO = '\033[33m'  # yellow
    ERROR = '\033[31m'  # red
    RESET = '\033[0m'  # reset


def msg_error(body):
    print(f"{fg.ERROR}{fg.BOLD}Error:{fg.RESET} {body}")
    sys.exit(1)


def msg_info(body):
    print(f"{fg.INFO}{fg.BOLD}Info:{fg.RESET} {body}")


def msg_ok(body):
    print(f"{fg.OK}{fg.BOLD}OK:{fg.RESET} {body}")


def sanity_checks():
    """Check if we are in a git repo, on the right branch and up-to-date"""
    is_git = run_command(['git', 'rev-parse', '--is-inside-work-tree'])
    if is_git != "true":
        msg_error("This is not a git repository.")

    current_branch = run_command(['git', 'rev-parse', '--abbrev-ref', 'HEAD'])
    if "release" in current_branch:
        msg_info(f"You are already on a release branch: {current_branch}")
    elif "rhel-8" in current_branch:
        msg_info(f"You are going for a point release against: {current_branch}")
    elif current_branch != "main":
        msg_error(f"You are not on the 'main' branch but on branch '{current_branch}'.")

    is_clean = run_command(['git', 'status', '--untracked-files=no', '--porcelain'])
    if is_clean != "":
        status = run_command(['git', 'status', '--untracked-files=no', '-s'])
        msg_info("The working directory is not clean.\n"
                 "You have the following unstaged or uncommitted changes:\n"
                 f"{status}")
    return current_branch


def run_command(argv):
    """Run a shellcommand and return stdout"""
    result = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding='utf-8').stdout
    return result.strip()


def step(action, args, verify):
    """Ask the user for confirmation on whether to accept (y) or skip (s) the step or cancel (N) the playbook"""
    feedback = input(f"{fg.BOLD}Step: {fg.RESET}{action} [y/s/N] ")
    if feedback == "y":
        if args is not None:
            out = run_command(args)
            if verify is not None:
                out = run_command(verify)

            msg_ok(f"\n{out}")
        return None
    elif feedback == "s":
        msg_info("Step skipped.")
        return "skipped"
    else:
        msg_info("Release playbook canceled.")
        sys.exit(0)


def autoincrement_version():
    """Bump the version of the latest git tag by 1"""
    latest_tag = run_command(['git', 'describe', '--abbrev=0'])
    if "." in latest_tag:
        version = latest_tag.replace("v", "").split(".")[0] + "." + str(int(latest_tag[-1]) + 1)
    else:
        version = int(latest_tag.replace("v", "")) + 1
    return version


def guess_remote(repo):
    """Guess the git remote to push the release changes to"""
    origin = f"github.com[/:]osbuild/{repo}.git"
    remotes = run_command(['git', 'remote']).split("\n")
    if len(remotes) > 2:
        msg_info("You have more than two 'git remotes' specified, so guessing the correct one will most likely fail.\n"
                 "Please use the --remote argument to set the correct one.\n"
                 f"{remotes}")

    for remote in remotes:
        remote_url = run_command(['git', 'remote', 'get-url', f'{remote}'])
        if search(origin, remote_url) is None:
            return remote
    return None


def get_milestone(api, version):
    milestones = api.issues.list_milestones()
    for milestone in milestones:
        if str(version) in milestone.title:
            msg_info(f"Gathering pull requests for milestone '{milestone.title}' ({milestone.url})")
            return milestone.number
    return None


def get_pullrequest_infos(api, milestone):
    prs = api.pulls.list(state="closed")
    i = 0
    pr_count = 0
    summaries = ""

    while i <= (len(prs)):
        i += 1
        prs = api.pulls.list(state="closed", page=i)

        for pr in prs:
            if pr.milestone is not None and pr.milestone.number == milestone:
                pr_count += 1
                print(f" * {pr.url}")
                summaries += f"  * {pr.title}: {pr.body}\n\n"

    msg_ok(f"Collected summaries from {pr_count} pull requests.")
    return summaries


def get_contributors(version):
    tag = run_command(['git', 'describe', '--abbrev=0'])
    contributors = run_command(["git", "log", '--format="%an"', f"{tag}..HEAD"])
    contributor_list = contributors.replace('"', '').split("\n")
    names = ""
    for name in set(sorted(contributor_list)):
        if name != "":
            names += f"{name}, "

    return names[:-2]


def get_unreleased(version):
    summaries = ""
    path = f'docs/news/{version}'
    files = [f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))]
    for file in files:
        with open(f'docs/news/{version}/{file}', 'r') as md:
            summaries += md.read() + "\n"

    return summaries


def update_news_osbuild(args):
    """Update the NEWS file for osbuild"""
    a = step(f"Update NEWS.md with pull request summaries for milestone {args.version}", None, None)
    if a == "skipped":
        return ""

    if args.token is None:
        msg_info("You have not passed a token so you may run into GitHub rate limiting.")

    api = GhApi(repo="osbuild", owner='osbuild', token=args.token)

    milestone = get_milestone(api, args.version)
    if milestone is None:
        msg_info(f"Couldn't find a milestone for version {args.version}")
        return ""
    else:
        summaries = get_pullrequest_infos(api, milestone)
        return summaries


def update_news_composer(args):
    """Update the NEWS file for osbuild-composer"""
    src = 'docs/news/unreleased'
    target = f'docs/news/{args.version}'
    step(f"Create '{target}' for this release and move all unreleased .md files to it", None, None)
    files = os.listdir(src)
    for file in files:
        shutil.move(os.path.join(src,file), target)
    msg_info(f"Content of docs/news/{args.version}:\n{run_command(['ls',f'docs/news/{args.version}'])}")

    step(f"Update NEWS.md with information from the markdown files in 'docs/news/{args.version}'", None, None)
    summaries = get_unreleased(args.version)
    return summaries


def update_news(args, repo):
    """Update the NEWS file"""
    today = date.today()
    contributors = get_contributors(args.version)

    if repo == "osbuild":
        summaries = update_news_osbuild(args)
    elif repo == "osbuild-composer":
        summaries = update_news_composer(args)
    
    filename = "NEWS.md"
    if (os.path.exists(filename)):
        with open(filename, 'r') as file:
            content = file.read()

        with open(filename, 'w') as file:
            file.write(f"## CHANGES WITH {args.version}:\n\n"
                       f"{summaries}"
                       f"Contributions from: {contributors}\n\n"
                       f"— Location, {today.strftime('%Y-%m-%d')}\n\n"
                       f"{content}")
    else:
        print(f"Error: The file {filename} does not exist.")


def bump_version(version, filename):
    """Bump the version in a file"""
    latest_tag = run_command(['git', 'describe', '--abbrev=0'])
    with open(filename, 'r') as file:
        content = file.read()

    # Maybe use re.sub in case the version appears a second time in the spec file
    content = content.replace(latest_tag.replace("v", ""), str(version))

    with open(filename, 'w') as file:
        file.write(content)


def create_pullrequest(args, repo, current_branch):
    """Create a pull request on GitHub from the fork to the main repository"""
    if args.user is None or args.token is None:
        msg_error("Missing credentials for GitHub. Without a token you cannot create a pull request.")

    url = f'https://api.github.com/repos/osbuild/{repo}/pulls'
    payload = {'head': f'{args.user}:release-{args.version}',
               'base': current_branch,
               'title': f'Prepare release {args.version}',
               'body': 'Tasks:\n- Bump version\n-Update news',
               }

    r = requests.post(url, json=payload, auth=(args.user, args.token))
    if r.status_code == 201:
        msg_ok(f"Pull request successfully created: {r.json()['url']}")
    else:
        msg_error(f"Failed to create pull request: {r.status_code}")


def release_playbook(args, repo, current_branch):
    """Execute all steps of the release playbook"""
    # FIXME: Currently this step silently fails if the release branch exists but is not checked out
    if "release" not in current_branch:
        step(f"Check out a new branch for the release {args.version}",
             ['git', 'checkout', '-b', f'release-{args.version}'],
             ['git','branch','--show-current'])

    a = step("Update the NEWS.md file", None, None)
    if a != "skipped":
        update_news(args, repo)

    step(f"Make the notes in NEWS.md release ready using {args.editor}", [f'{args.editor}', 'NEWS.md'], None)

    a = step(f"Bump the version where necessary ({repo}.spec, potentially setup.py)", None, None)
    if a != "skipped":
        bump_version(args.version, f"{repo}.spec")
        if repo == "osbuild":
            bump_version(args.version, "setup.py")

    print(f"{run_command(['git', 'diff'])}")
    step(f"Please review all changes {args.version}", None, None)

    if repo == "osbuild":
        step(f"Add and commit the release-relevant changes ({repo}.spec NEWS.md setup.py)",
             ['git', 'commit', f'{repo}.spec', 'NEWS.md', 'setup.py',
              '-s', f'-m {args.version}', f'-m "Release osbuild {args.version}"'], None)
    elif repo == "osbuild-composer":
        a = step(f"Add and commit the release-relevant changes ({repo}.spec NEWS.md setup.py)", None, None)
        if a != "skipped":
            run_command(['git', 'add', 'docs/news'])
            run_command(['git', 'commit', f'{repo}.spec', 'NEWS.md',
                        'docs/news/unreleased', f'docs/news/{args.version}', '-s',
                        f'-m {args.version}', f'-m "Release osbuild-composer {args.version}"'])

    step(f"Push all release changes to the remote '{args.remote}'",
         ['git', 'push', '--set-upstream', f'{args.remote}', f'release-{args.version}'], None)

    a = step(f"Create a pull request on GitHub for user {args.user}", None, None)
    if a != "skipped":
        create_pullrequest(args, repo, current_branch)

    step("Has the upstream pull request been merged?", None, None)

    step("Switch back to the main branch from upstream and update it",
         ['git','checkout','main','&&','git','pull'],
         ['git','branch','--show-current'])

    step(f"Tag the release with version 'v{args.version}'",
         ['git', 'tag', '-s', '-m', f'{repo} {args.version}', f'v{args.version}', 'HEAD'],
         ['git','describe',f'v{args.version}'])

    step("Push the release upstream", ['git', 'push', f'v{args.version}'], None)

    # TODO: Create a release on github


def main():
    # Do some initial sanity checking of the repository and its state
    current_branch = sanity_checks()

    # Get some basic fallback/default values
    repo = os.path.basename(os.getcwd())
    version = autoincrement_version()
    remote = guess_remote(repo)
    username = getpass.getuser()
    # FIXME: Currently only works with GUI editors (e.g. not with vim)
    editor = run_command(['git', 'config', '--default', '"${EDITOR:-gedit}"', '--global', 'core.editor'])

    parser = argparse.ArgumentParser()
    parser.add_argument("-v", "--version", help=f"Set the version for the release (Default: {version})", default=version)
    parser.add_argument(
        "-r", "--remote", help=f"Set the git remote on GitHub to push the release changes to (Default: {remote})",
        default=remote)
    parser.add_argument("-u", "--user", help=f"Set the username on GitHub (Default: {username})", default=username)
    parser.add_argument("-t", "--token", help="Set the GitHub token used to authenticate")
    parser.add_argument(
        "-e", "--editor", help=f"Set which editor shall be used for editing text (e.g. NEWS) files (Default: {editor})",
        default=editor)
    args = parser.parse_args()

    msg_info(f"Updating branch '{current_branch}' to avoid conflicts...\n{run_command(['git', 'pull'])}")

    # Run the release playbook
    release_playbook(args, repo, current_branch)


if __name__ == "__main__":
    main()
