# fr-core: sources and licence

The order of the words in `words.json` is derived from **Lexique 3.83**, a lexical database of French
(http://www.lexique.org), released under **Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0)**,
https://creativecommons.org/licenses/by-sa/4.0/.

> New, B., Pallier, C., Ferrand, L., & Matos, R. (2001). Une base de données lexicales du français contemporain sur internet: LEXIQUE. *L'Année Psychologique*, 101, 447-462.
>
> New, B., Pallier, C., Brysbaert, M., & Ferrand, L. (2004). Lexique 2: A new French lexical database.
> *Behavior Research Methods, Instruments, & Computers*, 36(3), 516-524.

What was taken: for each lemma, the frequency per million words in the film-subtitle and book corpora (`freqlemfilms2` +
`freqlemlivres`), used to rank lemmas and recorded as `freq_per_million`. Inflected forms were merged into one entry,
crude or violent words and exclamations were left out, and the remaining lemmas keep Lexique's order.

What is ours: the glosses, the example sentences, the part-of-speech and article labels.

Because the ranked list is derived from Lexique, **`words.json` and this folder are shared under CC BY-SA 4.0** (the same
licence, with this attribution, and a note of the changes above). The word list is distributed as a separate data work next to the program's code. Anything that redistributes this folder must keep
this file. The rest of the package is not covered by this licence.
