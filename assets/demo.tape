# Regenerate the README demo GIF:
#   brew install vhs duckdb
#   uv sync --extra dev
#   cd /tmp && mkdir m2d-gif-demo && cd m2d-gif-demo && cp -r <repo>/examples .
#   vhs <repo>/assets/demo.tape
# (recorded from a scratch directory so the printed paths in the output don't
#  leak a real machine's home directory)
#
# Then shrink it before committing -- vhs writes a ~2.5MB GIF, and a reduced
# palette at a lower frame rate cuts that by more than half with no visible
# loss on terminal text:
#   ffmpeg -i demo.gif -vf "fps=12,palettegen=max_colors=32:stats_mode=diff" -y pal.png
#   ffmpeg -i demo.gif -i pal.png \
#     -lavfi "fps=12[x];[x][1:v]paletteuse=dither=none:diff_mode=rectangle" \
#     -y <repo>/assets/demo.gif

Output demo.gif

Set Shell "zsh"
Set FontSize 16
Set Width 1200
Set Height 700
Set Padding 24
Set Framerate 20
Set Theme {"name": "jba", "black": "#182333", "red": "#e5989b", "green": "#7cc576", "yellow": "#FA9306", "blue": "#2196F0", "purple": "#A8C9EE", "cyan": "#2196F0", "white": "#F6F8FB", "brightBlack": "#3b4252", "brightRed": "#e5989b", "brightGreen": "#7cc576", "brightYellow": "#FA9306", "brightBlue": "#2196F0", "brightPurple": "#A8C9EE", "brightCyan": "#2196F0", "brightWhite": "#FFFFFF", "background": "#0e1117", "foreground": "#F6F8FB", "cursor": "#FA9306"}
Set TypingSpeed 22ms

Hide
Type "source /path/to/model2data/.venv/bin/activate && clear"
Enter
Sleep 500ms
Show

# 1. Schema in -- synthetic data and a whole dbt project out.
Type "model2data --file examples/ecommerce.dbml --rows 200 --seed 42"
Sleep 400ms
Enter
Sleep 4000ms

Type "clear"
Enter
Sleep 300ms

# 2. One command: loads the seeds, builds the models, runs every test.
Type "cd dbt_ecommerce && dbt build"
Sleep 300ms
Enter
Sleep 5500ms

Type "clear"
Enter
Sleep 300ms

# 3. The payoff. Real names and emails, not lorem ipsum -- and a join that
#    actually resolves, because the foreign keys point at real parent rows.
Type `duckdb ecommerce_profile.duckdb -c "from staging.stg_customers select first_name, last_name, email, country limit 5"`
Sleep 300ms
Enter
Sleep 3000ms

Type `duckdb ecommerce_profile.duckdb -c "from staging.stg_customers c join staging.stg_orders o on o.customer_id = c.id select c.country, count(*) orders, round(sum(o.total_amount)) revenue group by 1 order by revenue desc limit 5"`
Sleep 300ms
Enter
Sleep 4000ms
