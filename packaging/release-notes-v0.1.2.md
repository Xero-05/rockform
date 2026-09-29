Fixes suggestions that mistook printable textured plates for natural grips, and replaces the fixed-width upper skirt with a gradual wall return.

### Download

Download **Rockform-0.1.2-Windows-x64.zip**, choose **Extract All**, and run **Rockform.exe**. Keep `_internal` beside it. No Python or Docker installation is needed.

### Changed

- Suggestions are screened on the retained rock surface after trimming. Broad relief and a continuous interior contact slope are required; backing and generated edges never count as natural grip.
- Detection starts around broad features and includes more low surrounding rock. A normal **No suitable broad handhold features found** result replaces misleading usable-hold messages.
- New support returns gradually to the wall, with width increasing for higher cut edges. Substantial artificial ledges are rejected. Oversized support excludes an automatic suggestion.
- Undrilled previews show **green rock and amber generated support**. Manual shapes remain possible, with clear explanations when they fail the handhold screen.
- Grip and support measurements are saved and included in export reports separately from closed-solid validation. Unresolved scan-up orientation is called out.
- Editable bolt and screw profiles remain available. New mount centers stay inside the scanned body. Existing hardware dimensions and manual placements survive rebuilding.

### Existing designs

Old projects keep their original geometry until explicitly changed. Select a hold in **2 Holds** and choose **Use gradual wall return** to upgrade its edge. Some old selections will require a different boundary; a failed rebuild leaves the original unchanged. New saves use format 3 and require Rockform 0.1.2 or later. Save a new copy to retain compatibility with older versions.

### Validation and limits

**52 automated tests passed.** Regressions cover flat/noisy surfaces, post-trim feature loss, positive broad ledges and rounded features, artificial shelves, corner closure, original-surface preservation, manual hardware, saving/reopening, exports and the native interface. The acceptance workflow exercises three synthetic scan-to-export cases and the supplied Scaniverse scan.

The supplied real scan currently produces **no suitable automatic handholds** at default settings: retained low-relief patches are rejected and other selections have unresolved geometry. This is an honest screening result, not a claim that all real rock features were exhaustively assessed. Independent PC, slicer, print and climbing-use validation remain outstanding. These thresholds are prototype heuristics; the software cannot guarantee grip quality, prevent every unintended grip, or qualify climbing equipment.
