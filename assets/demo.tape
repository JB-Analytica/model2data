# Regenerate the README demo GIF:
#   brew install vhs            (jq ships with macOS 15; elsewhere install it too)
#   uv sync --extra dev
#   cd /tmp && mkdir m2d-gif-demo && cd m2d-gif-demo && cp -r <repo>/examples .
#   vhs <repo>/assets/demo.tape
# (recorded from a scratch directory so the printed paths in the output don't
#  leak a real machine's home directory; replace /path/to/model2data below with
#  the checkout whose .venv should run)
#
# Commit the GIF as vhs writes it (about 250 KB at this size). A reduced
# palette makes it smaller but turns dbt's green PASS and jq's colours grey.

Output demo.gif

Set Shell "zsh"
Set FontSize 16
Set Width 1520
Set Height 540
Set Padding 24
Set Framerate 20
Set Theme {"name": "jba", "black": "#182333", "red": "#e5989b", "green": "#7cc576", "yellow": "#FA9306", "blue": "#2196F0", "purple": "#A8C9EE", "cyan": "#2196F0", "white": "#F6F8FB", "brightBlack": "#3b4252", "brightRed": "#e5989b", "brightGreen": "#7cc576", "brightYellow": "#FA9306", "brightBlue": "#2196F0", "brightPurple": "#A8C9EE", "brightCyan": "#2196F0", "brightWhite": "#FFFFFF", "background": "#0e1117", "foreground": "#F6F8FB", "cursor": "#FA9306"}
Set TypingSpeed 22ms

Hide
Type "source /path/to/model2data/.venv/bin/activate && clear"
Enter
Sleep 500ms
Show

# 1. A model and its metrics in -- synthetic data, a dbt project and each
#    metric's known value out.
Type "model2data -f examples/ecommerce.model2data.yml --metrics examples/ecommerce.metrics.yml --rows 200 --seed 42 --as-of 2026-01-31"
Sleep 400ms
Enter
Sleep 3500ms

Type "clear"
Enter
Sleep 300ms

# 2. One command loads the seeds, builds the models and runs every test. The
#    full log is 120 lines; the grep keeps the metric tests (each one computes
#    its metric on the warehouse and must match the known value) and dbt's
#    summary line, so both stay on screen long enough to read.
Type `cd dbt_ecommerce && dbt build --use-colors | grep -E "PASS metric_|Done\."`
Sleep 300ms
Enter
Sleep 7500ms

# 3. The known values themselves: what a dashboard on this data must show.
Type `jq '.metrics | map_values(.value)' metric_values.json`
Sleep 300ms
Enter
Sleep 4500ms
