#!/bin/sh
# tpgyx@gmail.com
# This script updates all branches of a package in github

# update repos in github
for i in `ls -d */`; do
    pushd ${i%?}
	if `ls *.spec >/dev/null`;  then
	    for branch in `git branch -a | grep remotes | grep -v HEAD | grep -v master`; do
		git branch --track ${branch##*/} $branch
	    done
	    git fetch --all
	    git pull --all
#	    git remote set-url --push origin git@github.com:OpenMandrivaAssociation/${i%?}.git
	    git remote set-url --add --push origin git@github.com:OpenMandrivaAssociation/${i%?}.git
	    git remote set-url --add --push origin git@abf.io:openmandriva/${i%?}.git
	    git push
	    git push --mirror git@github.com:OpenMandrivaAssociation/${i%?}.git
	else
	    echo "No spec file found"
	fi
    popd
done
