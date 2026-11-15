package main

import (
 "encoding/json"
 "fmt"
 "go/ast"
 "go/parser"
 "go/token"
 "os"
 "reflect"
)

var position = reflect.TypeOf(token.Pos(0))

func normalize(v reflect.Value) any {
 if !v.IsValid() { return nil }
 if v.Type() == position { return v.Int() != 0 }
 if v.Kind() == reflect.Interface { if v.IsNil() { return nil }; return normalize(v.Elem()) }
 if v.Kind() == reflect.Pointer {
  if v.IsNil() { return nil }
  if p, ok := v.Interface().(*ast.ParenExpr); ok { return normalize(reflect.ValueOf(p.X)) }
  return normalize(v.Elem())
 }
 switch v.Kind() {
 case reflect.Struct:
  result := map[string]any{"node_type": v.Type().String()}
  for i:=0; i<v.NumField(); i++ { result[v.Type().Field(i).Name]=normalize(v.Field(i)) }
  return result
 case reflect.Slice:
  if v.IsNil() { return nil }
  result:=make([]any,v.Len()); for i:=range result { result[i]=normalize(v.Index(i)) }; return result
 default: return v.Interface()
 }
}

func main() {
 if len(os.Args)!=2 { panic("Use one exact source file") }
 tree,err:=parser.ParseFile(token.NewFileSet(),os.Args[1],nil,parser.ParseComments|parser.SkipObjectResolution)
 if err!=nil { panic(err) }
 raw,err:=json.Marshal(normalize(reflect.ValueOf(tree))); if err!=nil { panic(err) }
 fmt.Println(string(raw))
}
