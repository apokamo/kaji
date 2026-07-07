# Regenerate the README terminal demo:
#
#   vhs docs/assets/demo.tape
#
# The rendered video is written to docs/assets/demo.mp4. Keep the output as MP4
# rather than GIF so terminal text remains readable in the README.

Output docs/assets/demo.mp4

Set Shell "bash"
Set FontSize 18
Set Width 1200
Set Height 720
Set Padding 18
Set TypingSpeed 45ms

Type "kaji --help"
Enter
Sleep 2s

Type "kaji validate .kaji/wf/dev.yaml .kaji/wf/docs.yaml"
Enter
Sleep 2s

Type "kaji run .kaji/wf/dev.yaml 269 --before review-ready --quiet"
Enter
Sleep 2s

Type "kaji run .kaji/wf/docs.yaml 269 --before review-ready --quiet"
Enter
Sleep 2s
