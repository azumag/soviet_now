#!/usr/bin/env perl
# Preserve the status display's sanitization/filter order in one
# process. Input is the bounded log tail or current START/END output block.
use strict;
use warnings;

my ($fallback) = @ARGV;
while (my $line = <STDIN>) {
    $line =~ s/\e\[[0-9;]*[a-zA-Z]//g;
    $line =~ s/\r//g;
    $line =~ s/[\x00-\x08\x0B-\x1F\x7F]//g;
    chomp $line;
    next if $line =~ /^[[:space:]]*$/;
    next if $line =~ /opencode thinking/;
    next if $line =~ /^Continue if you have next steps/;
    next if $line =~ /^[[:space:]]*(?:✱|→|←) /;
    if ($fallback) {
        next if $line =~ /\[IMPROVE\] job start|\[IMPROVE\] attached pid=/;
    }
    print "$line\n";
}
