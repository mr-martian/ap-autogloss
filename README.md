# ap-autogloss
Generate IGT from Apertium stream format

## Input

The script expects input from a bidix with surface forms.
This can be accomplished with a mode such as the following:

```xml
  <mode name="eng-spa-gloss">
    <pipeline>
      <!-- analyze (probably with dictionary case) -->
      <program name="lt-proc -w">
        <file name="eng-spa.automorf.bin"/>
      </program>
      <!-- tag, leaving surface forms -->
      <program name="apertium-tagger -gp">
        <file name="eng-spa.prob"/>
      </program>
      <!-- bidix, with surface forms in input and output -->
      <program name="lt-proc -O">
        <file name="eng-spa.autobil.bin"/>
      </program>
    </pipeline>
  </mode>
```

This can then be piped into the script along the lines of

```shell
$ echo 'I am singing and he does sing' | apertium eng-spa-gloss | python3 autogloss.py eng.toml
I      am  singing    and he     does           sing        .
prpers ser cantar-GER y   prpers (do)[PRES]-3SG cantar[INF] .
```

If you have a morpheme segmentation mode for the source language, you can pass it to the script with the `--seg-mode` flag (and `--seg-mode-directory` if it's not installed).
In theory, this will add a middle line with morphemes, but I haven't particularly tested this yet.
