if exists('b:current_syntax')
  finish
endif


syntax match Comment '\v^\#.*$'

syntax match Include '\v^extends\s' contains=Delimiter
syntax match Delimiter '\V,'

syntax match Label '\v^abbr\s'
syntax match Keyword '\v^snippet\s|^alias\s'

syntax match String '\v^\s+.*$' contains=Special
syntax match Special '\v\$\{[^\}]+\}' contains=Conditional
syntax match Conditional '\V:'


let b:current_syntax = expand('<sfile>:t:r')
echom b:current_syntax
