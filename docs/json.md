# JSON interface

Every command accepts `--json`. Errors go to stderr as `{"error": "..."}` with exit code 1. Shapes below show the
fields a reader may rely on; unknown fields must be ignored, and fields are only added within a version.

| Command | Result (top-level keys) |
|---|---|
| `init`, `config show/set` | `learner, root, lang, profile` / the config or profile object |
| `formats [exam]` | `exams[], categories` / the exam with `tasks[]` (`id, skill, name, answer, minutes, mini{minutes,hint}, prep_s, response_s, words, hard, hint, criteria`) |
| `status` | `lang, now, open_sessions[], cards{total,due_now,due_today,suspended,mature,by_state}, sessions_finished, last_finished, speaking_debt, step, foundations, words` |
| `overview` | `languages[], lang, status, session, plan, attempts[]`: the status of `lang` (the `--lang` asked for when it is set up, else the first), its open session as `session show` gives it with each attempt as `attempt show` gives it, or `plan` when no session is open; `lang` and the rest are null with no language yet |
| `plan` | `lang, generated, minutes_available, minutes_planned, step, light, stage (foundations\|exam\|null), foundations{lessons_done,lessons_total,complete,next_lesson}, cards_due, priorities[{skill,score,reasons[]}], items[], smallest_step`; items are `review{cards,new,new_cards}`, `lesson{lesson,unit,name}`, `task`, `drill` |
| `tracks [id]` | `tracks[{track,name,lang,kind,explain,lessons,units\|words}]` / the track |
| `lesson list` | `status{stage,forced,lessons_done,lessons_total,complete,next_lesson,units[{track,id,title,done,total}],words_track}, lessons[{id,unit,title,minutes,done}]` |
| `lesson show [id]` | `id, track, unit, lang, title, minutes, explain, items[{id,say,show,gloss,hint,pair,drill,audio[{text,file,slow_file}]}], cards, times_done, attempts[{id,created,items,status,reliability,repeat}] (the last five repeats), audio_status{status,built,failed}`; files are relative to the language folder (`<data>/<lang>/`) |
| `lesson done [id]` | `lesson, cards_added, cards_due_from` (cards are due from the next local day; repeating in one session changes nothing) |
| `audio build [--lesson\|--track\|--limit]` | `status (ok\|partial\|not_configured), built, cached, failed[], total` |
| `audio check [--lesson]` | `lang, checks[{name,score,scores,heard,expected,items}], mean_score` (needs speech-check) |
| `words status\|list\|intro` | `track,name,total,in_deck,known,new_today,cap_per_day,remaining,next[],bands[{band,from,to,total,in_deck,known,met}]` / `words[]` / `introduced[]` (card views) |
| `reading plan` | `topic{id,label,last_read}, domain, words{track,band,per_reading,target[{id,word,gloss,rank}],pool[],known[],seen[]}, recent[], readings_so_far` |
| `reading add\|list` | the stored reading `{id,ts,topic,domain,title,source,new_words,file}` / `readings[]` |
| `session start/show/finish/reopen/abandon/points` | `id, lang, status, started, finished, plan{items[] with state}, attempts[], cards_reviewed, focused_minutes, activity, points{points, parts, capped, what, status, trophies[], response}` |
| `session prompt` | the session, with the task item's optional `prompt` text set (`--item i1`, `--text` or `--text-file`; empty removes it); `attempt add --item` stores it as the attempt's prompt |
| `session list` | `sessions[{id,status,started,finished,focused_minutes,points}]` |
| `attempt add/feedback` | `attempt{id,session,item,skill,task,variant,kind,word_count,within_word_range,minutes,metrics,estimate,folder}, speech{status,error,warnings,reliability (normal\|low),repeat{words_in_reference,matched,word_accuracy,omitted,substituted,flagged[{word,expected,heard,flag}],reliability,note},summary_text,transcript,file,sound_cards_new[]} or null, errors{recorded,ids,cards_new[],cards_recurring[]}, error_ids[]` |
| `attempt show` | the attempt plus `answer_text, feedback, speech_summary, folder` |
| `review due` | `lang, due_total, shown, cards[{id,kind,prompt,hint,category,you_wrote,occurrences,new,answer,full}]` (`--no-answer` drops `answer`, `full`); `listen` cards add `mode, audio{text,side,file,slow_file}, choices, show, gloss, lesson`; `word` cards add `mode, audio, gloss, pos, word, rank, track, example{text,gloss,file,slow_file}` |
| `review grade` | `graded[{card,rating,next_due,state,stability_days}], session, due_remaining` |
| `review stats` | the `cards` object of `status` |
| `cards list/show/add/suspend/resume` | `cards[]` / the stored card / `{card, new}`; `add --word W [--gloss JSON] [--article] [--example] [--example-gloss] [--source]` adds a word card (from the list, or your own) |
| `errors` | `lang, total, by_category{}, recurring[{pattern,category,count,attempts,first_seen,last_seen,skills,examples[]}], errors[]` |
| `progress` | see below; `--recent N` adds `facts` (this language's block of `facts`) and `recent{sessions[], attempts[]}`, the last N of each, newest first, as `session list` and `attempt show` give them |
| `facts` | [facts.md](facts.md) |

## progress

```
schema_version, learner, lang, generated, profile{exam,target,steps,levels,dreaded}, step,
sessions{finished,open,abandoned,last_finished,focused_minutes},
skills{listening|reading|writing|speaking: {attempts, voice_attempts, last_attempt, days_since_last, tasks[],
   metrics{name: {n, last, direction (up|down|flat|insufficient), change, recent_mean, earlier_mean,
                  same_task{task, previous_mean, attempts}, series[{ts,value,task}], better (lower|higher|null)}},
   estimates[{ts,low,high,task}]}},
errors{total, by_category, recurring[]}, sounds[{sound,expected,heard,times_flagged,first_flagged,last_flagged,clean_streak}],
speaking_debt{days_since_voice_answer, limit_days, owed, voice_answers_last_14_days},
cards{total,due_now,due_today,suspended,mature,by_state}
```

`direction` compares the mean of the last three values with the three before. `better` says which direction is
usually better; there is no calibrated "good" value, so trends are for comparing a learner with themselves on the same
task (`same_task`). Do not turn a number into a score.

## Input formats

`attempt add --errors FILE|-`: a JSON list (or `{"errors": [...]}`) of

```json
{"category": "grammar.agreement", "pattern": "subject-verb agreement", "wrong": "people who has",
 "right": "people who have", "sentence": "Many people who has children like it.",
 "corrected": "Many people who have children like it.", "note": "plural subject", "card": "auto"}
```

At least one of `wrong`, `right`, `note`; `card` is `auto` (default), `cloze`, `correction` or `none`; `skill`
overrides the attempt's skill. Give `wrong` and `right` as the smallest fragments that show the error and
`corrected` as the full corrected sentence, so the cloze can sit inside real context.
