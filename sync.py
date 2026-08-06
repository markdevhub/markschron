import subprocess
import sys

def run_git_command(cmd, check=True):
    """Runs a git command and returns the output."""
    try:
        # Run the command and capture standard output and standard error
        result = subprocess.run(cmd, shell=True, text=True, capture_output=True)
        if check and result.returncode != 0:
            print(f"Error running command: {cmd}")
            print(result.stderr)
            sys.exit(1)
        return result.stdout.strip()
    except Exception as e:
        print(f"Execution failed: {e}")
        sys.exit(1)

def main():
    print("Fetching latest changes from upstream (fmhy/edit)...")
    
    # 1. Make sure the upstream remote is added
    remotes = run_git_command("git remote")
    if "upstream" not in remotes:
        print("Adding upstream remote...")
        run_git_command("git remote add upstream https://github.com/fmhy/edit.git")

    # 2. Fetch the latest from upstream
    run_git_command("git fetch upstream main")

    # 3. Get the list of changed files in the docs/ folder
    # This compares your current branch to the newly fetched upstream/main
    diff_output = run_git_command("git diff --name-only HEAD upstream/main -- docs/")
    
    if not diff_output:
        print("No updates found in the docs/ directory. Everything is up to date!")
        return

    # 4. Filter strictly for top-level .md files in docs/
    updated_files = []
    for file in diff_output.split('\n'):
        if file.endswith('.md'):
            # Counting the slashes ensures it is exactly inside docs/ 
            # (e.g., 'docs/ai.md' has 1 slash. 'docs/posts/ai.md' has 2)
            if file.count('/') == 1:
                updated_files.append(file)

    if not updated_files:
        print("Updates were found in subfolders, but no top-level .md files need updating.")
        return

    # 5. Show the files and ask for permission
    print("\n📦 The following top-level .md files have updates available:\n")
    for file in updated_files:
        print(f"  - {file}")
    
    choice = input("\nDo you want to pull these changes into your local repo? (y/n): ").strip().lower()
    
    if choice == 'y':
        print("\nUpdating files...")
        
        # 6. Checkout and stage each specific file
        for file in updated_files:
            run_git_command(f"git checkout upstream/main -- {file}")
            run_git_command(f"git add {file}")
            
        print("\n✅ Success! The files have been updated and staged.")
        print("You can now run 'git commit' in your terminal or use VS Code to finalize the commit.")
    else:
        print("\n❌ Update cancelled. Your files have not been changed.")

if __name__ == "__main__":
    main()