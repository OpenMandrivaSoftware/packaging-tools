#include <KArchive>
#include <KTar>
#include <KZip>
#include <K7Zip>
#include <QString>

#include <iostream>

static QStringList findProvides(KArchiveDirectory const &dir);
static QStringList findProvides(KArchiveDirectory const &dir) {
	QStringList provs;
	for(QString const &s: dir.entries()) {
		KArchiveEntry const *e = dir.entry(s);
		if(e->isDirectory()) {
			provs << findProvides(static_cast<KArchiveDirectory const &>(*e));
			continue;
		}
		if(s.toLower().startsWith("find") && s.toLower().endsWith(".cmake"))
			provs << QString("cmake(" + s.mid(4, s.length()-10) + ")");
		else if(s.toLower().endsWith("-config.cmake"))
			provs << QString("cmake(" + s.left(s.length()-13) + ")");
		else if(s.toLower().endsWith("config.cmake"))
			provs << QString("cmake(" + s.left(s.length()-12) + ")");
	}
	provs.removeDuplicates();
	return provs;
}

static QStringList findDeps(KArchiveDirectory const &dir);
static QStringList findDeps(KArchiveDirectory const &dir) {
	QStringList deps;
	for(QString const &s: dir.entries()) {
		KArchiveEntry const *e = dir.entry(s);
		if(e->isDirectory()) {
			for(QString const &dep : findDeps(static_cast<KArchiveDirectory const &>(*e)))
				if(!deps.contains(dep))
					deps << dep;
			continue;
		}
		if(!e->isFile())
			continue;
		if(s.toLower() == "cmakelists.txt" || s.toLower().endsWith(".cmake")) {
			KArchiveFile const *f = static_cast<KArchiveFile const *>(e);
			QStringList const c=QString(f->data()).split("\n");
			for(QStringList::ConstIterator it=c.begin(); it!=c.end(); ++it) {
				QString line = *it;
				while(line.count("(") != line.count(")")) {
					line += *(++it);
				}
				line = line.simplified();
				if(line.toLower().startsWith("find_package(") || line.toLower().startsWith("find_package (")) {
					QString dep = line.section('(', 1).section(')', 0, 0);
					QStringList args = dep.split(' ');
					// We're nowhere near smart enough to handle a dependency on
					// something defined in a cmake variable...
					if(args[0].contains('$'))
						continue;
					bool components=false;
					for(int i=1; i<args.size(); i++) {
						if(args[i] == "COMPONENTS")
							components=true;
						else if(args[i].startsWith('#'))
							break;
						else if(components)
							deps << QString("cmake(" + args[0] + args[i] + ")");
					}
					if(!components)
						deps << QString("cmake(" + args[0] + ")");
				}
			}
		}
	}
	deps.removeDuplicates();
	return deps;
}


int main(int argc, char **argv) {
	if(argc<2) {
		std::cerr << "Usage:" << std::endl
			<< argv[0] << " sourcetarball.tar.xz" << std::endl;
		return 1;
	}
	QString file = argv[1];
	KArchive *ar;
	if(file.toLower().endsWith(".zip"))
		ar = new KZip(file);
	else if(file.toLower().endsWith(".7z"))
		ar = new K7Zip(file);
	else
		ar = new KTar(file);

	if(!ar) {
		std::cerr << "Error creating KArchive instance" << std::endl;
		return 2;
	}

	if(!ar->open(QIODevice::ReadOnly)) {
		std::cerr << "Can't open " << qPrintable(file) << ": " << qPrintable(ar->errorString()) << std::endl;
		return 3;
	}

	KArchiveDirectory const *d = ar->directory();
	if(!d) {
		std::cerr << "Can't find directory in archive" << std::endl;
		return 4;
	}

	QStringList BRs = findDeps(*d);
	QStringList provides = findProvides(*d);

	// We don't want to depend on ourselves...
	for(QString const &dep : provides)
		BRs.removeAll(dep);

	for(QString const &dep : BRs)
		std::cout << "BuildRequires: " << qPrintable(dep) << std::endl;

	return 0;
}
