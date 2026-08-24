# Regenerate the README demo GIF:
#   brew install vhs
#   uv sync --extra dev
#   cd /tmp && mkdir m2d-gif-demo && cd m2d-gif-demo && cp -r <repo>/examples .
#   vhs <repo>/assets/demo.tape
# (recorded from a scratch directory so the printed paths in the output don't
#  leak a real machine's home directory)

Output demo.gif

Set Shell "zsh"
Set FontSize 16
Set Width 1200
Set Height 700
Set Padding 24
Set Theme {"name": "jba", "black": "#182333", "red": "#e5989b", "green": "#7cc576", "yellow": "#FA9306", "blue": "#2196F0", "purple": "#A8C9EE", "cyan": "#2196F0", "white": "#F6F8FB", "brightBlack": "#3b4252", "brightRed": "#e5989b", "brightGreen": "#7cc576", "brightYellow": "#FA9306", "brightBlue": "#2196F0", "brightPurple": "#A8C9EE", "brightCyan": "#2196F0", "brightWhite": "#FFFFFF", "background": "#0e1117", "foreground": "#F6F8FB", "cursor": "#FA9306"}
Set TypingSpeed 45ms

Hide
Type "source /path/to/model2data/.venv/bin/activate && clear"
Enter
Sleep 500ms
Show

Type "model2data --file examples/hackernews.dbml --rows 200 --seed 42"
Sleep 500ms
Enter
Sleep 4500ms

Type "clear"
Enter
Sleep 300ms

Type "cd dbt_hackernews && dbt seed --quiet && dbt run"
Sleep 300ms
Enter
Sleep 3500ms

Sleep 2000ms
